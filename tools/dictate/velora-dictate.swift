// Dictée vocale de Velora sur Mac : micro -> texte, entièrement sur l'appareil (API SpeechAnalyzer de macOS 26, modèle de langue géré par macOS).
// Velora lance ce petit programme et lit ses lignes JSON sur la sortie standard ; la ligne « stop » sur l'entrée standard l'arrête.
//   velora-dictate [--locale fr-FR]          dicte depuis le micro
//   velora-dictate --file son.wav            transcrit un fichier audio
//   velora-dictate --check                   {"available":…,"installed":…}
//   velora-dictate --install                 installe la langue (téléchargement géré par macOS)
// Sorties : {"t":"ready"} {"t":"partial","text":…} {"t":"final","text":…} {"t":"done"} {"t":"error","code":…,"msg":…}
import Speech
import AVFoundation
import Foundation

func emit(_ o: [String: Any]) {
    if let d = try? JSONSerialization.data(withJSONObject: o), let s = String(data: d, encoding: .utf8) { print(s); fflush(stdout) }
}

func fail(_ code: String, _ msg: String) -> Never {
    emit(["t": "error", "code": code, "msg": msg]); exit(1)
}

final class Control: @unchecked Sendable {
    var engine: AVAudioEngine?
    var cont: AsyncStream<AnalyzerInput>.Continuation?
    var analyzer: SpeechAnalyzer?
    private var stopped = false
    private let lock = NSLock()
    func stop() {
        lock.lock(); defer { lock.unlock() }
        if stopped { return }
        stopped = true
        engine?.stop(); engine?.inputNode.removeTap(onBus: 0)
        cont?.finish()
        let a = analyzer
        Task { try? await a?.finalizeAndFinishThroughEndOfInput() }
    }
}

@main struct Dictate {
    static func main() async {
        let args = CommandLine.arguments
        let localeId = args.drop(while: { $0 != "--locale" }).dropFirst().first ?? "fr-FR"
        guard SpeechTranscriber.isAvailable else {
            if args.contains("--check") { emit(["available": false, "installed": false]); return }
            fail("unavailable", "La reconnaissance vocale n'est pas disponible sur ce Mac.")
        }
        guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: localeId)) else {
            if args.contains("--check") { emit(["available": false, "installed": false]); return }
            fail("locale", "Langue non prise en charge : \(localeId)")
        }
        let installed = await SpeechTranscriber.installedLocales.contains { $0.identifier == locale.identifier }
        if args.contains("--check") { emit(["available": true, "installed": installed]); return }
        let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: args.contains("--file") ? [] : [.volatileResults], attributeOptions: [])
        if args.contains("--install") {
            do {
                if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) { try await req.downloadAndInstall() }
                emit(["t": "done"])
            } catch { fail("install", "\(error)") }
            return
        }
        if !installed { fail("asset_missing", "La langue n'est pas installée sur ce Mac.") }
        do {
            if let path = args.drop(while: { $0 != "--file" }).dropFirst().first { try await transcribeFile(path, transcriber) }
            else { try await dictate(transcriber) }
        } catch { fail("failed", "\(error)") }
    }

    static func transcribeFile(_ path: String, _ transcriber: SpeechTranscriber) async throws {
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        let reader = Task { for try await r in transcriber.results where r.isFinal { emit(["t": "final", "text": String(r.text.characters)]) } }
        if let last = try await analyzer.analyzeSequence(from: file) { try await analyzer.finalizeAndFinish(through: last) } else { await analyzer.cancelAndFinishNow() }
        try await reader.value
        emit(["t": "done"])
    }

    static func dictate(_ transcriber: SpeechTranscriber) async throws {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: break
        case .notDetermined: if !(await AVCaptureDevice.requestAccess(for: .audio)) { fail("mic_denied", "Accès au micro refusé.") }
        default: fail("mic_denied", "Accès au micro refusé.")
        }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        guard let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else { fail("format", "Format audio indisponible.") }
        let (stream, cont) = AsyncStream.makeStream(of: AnalyzerInput.self)
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let inFmt = input.outputFormat(forBus: 0)
        guard inFmt.sampleRate > 0, let conv = AVAudioConverter(from: inFmt, to: fmt) else { fail("mic", "Aucun micro utilisable.") }
        let control = Control(); control.engine = engine; control.cont = cont; control.analyzer = analyzer
        input.installTap(onBus: 0, bufferSize: 4096, format: inFmt) { buf, _ in
            let cap = AVAudioFrameCount(Double(buf.frameLength) * fmt.sampleRate / inFmt.sampleRate) + 64
            guard let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: cap) else { return }
            var supplied = false
            var err: NSError?
            conv.convert(to: out, error: &err) { _, st in
                if supplied { st.pointee = .noDataNow; return nil }
                supplied = true; st.pointee = .haveData; return buf
            }
            if err == nil, out.frameLength > 0 { cont.yield(AnalyzerInput(buffer: out)) }
        }
        try await analyzer.start(inputSequence: stream)
        engine.prepare(); try engine.start()
        emit(["t": "ready"])
        Thread.detachNewThread {  // « stop » sur l'entrée standard, ou fermeture de l'entrée (Velora s'est arrêté) : on termine proprement
            while let l = readLine() { if l.trimmingCharacters(in: .whitespaces) == "stop" { break } }
            control.stop()
        }
        for try await r in transcriber.results { emit(["t": r.isFinal ? "final" : "partial", "text": String(r.text.characters)]) }
        emit(["t": "done"])
    }
}
