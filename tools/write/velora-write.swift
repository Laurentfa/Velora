// Corriger et reformuler un texte avec Apple Intelligence (modèle local de macOS 26) : gratuit, sur l'appareil, sans IA distante.
//   velora-write --check                       {"available":true} ou {"available":false,"reason":…}
//   echo '{"mode":"correct","text":"…"}' | velora-write      {"text":"…"} ou {"error":"code","msg":"…"}
// Modes : correct, rephrase, formal, friendly, shorter, expand
import FoundationModels
import Foundation

func emit(_ o: [String: Any]) {
    if let d = try? JSONSerialization.data(withJSONObject: o), let s = String(data: d, encoding: .utf8) { print(s); fflush(stdout) }
}

let COMMON = "Tu écris en français. Tu ne dois RIEN inventer : aucun fait, nom, date, montant ni engagement qui ne soit pas dans le texte. Tu gardes le sens, les noms propres, les chiffres, le tutoiement ou le vouvoiement de l'original. Celui qui parle reste celui qui parle (je reste je), et une question ou une demande reste une question ou une demande : ne la transforme JAMAIS en affirmation ni en confirmation. Tu réponds UNIQUEMENT par le texte final, sans commentaire, sans guillemets, sans introduction."
let MODES: [String: String] = [
    "correct": "Corrige l'orthographe, la grammaire, les accords et la ponctuation du texte. Ne change ni le style ni les mots sauf pour corriger une faute.",
    "rephrase": "Reformule le texte avec d'autres mots et d'autres tournures de phrase, plus clairs et mieux écrits, en gardant le même sens et le même registre. Tu dois changer la formulation de la plupart des phrases : ne rends jamais le texte à l'identique.",
    "formal": "Réécris le texte dans un registre professionnel et poli (vouvoiement), sans l'allonger inutilement.",
    "friendly": "Réécris le texte avec un ton nettement plus chaleureux et cordial (formules aimables, mots bienveillants), tout en restant professionnel. Tu dois changer la formulation : ne rends jamais le texte à l'identique.",
    "shorter": "Raccourcis le texte : garde l'essentiel et les informations utiles, supprime le superflu.",
    "expand": "Développe légèrement le texte pour le rendre plus complet et agréable à lire, uniquement avec des formulations de politesse ou de liaison : n'ajoute aucune information nouvelle. Tu dois réécrire avec des phrases plus complètes : ne rends jamais le texte à l'identique.",
]

@main struct W {
    static func main() async {
        let model = SystemLanguageModel.default
        if case .unavailable(let reason) = model.availability {
            if CommandLine.arguments.contains("--check") { emit(["available": false, "reason": "\(reason)"]); return }
            emit(["error": "unavailable", "msg": "\(reason)"]); exit(1)
        }
        if CommandLine.arguments.contains("--check") { emit(["available": model.supportsLocale(Locale(identifier: "fr_FR"))]); return }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let o = try? JSONSerialization.jsonObject(with: input) as? [String: Any], let mode = o["mode"] as? String, let text = o["text"] as? String, let task = MODES[mode] else {
            emit(["error": "input", "msg": "Entrée invalide"]); exit(1)
        }
        do {
            let session = LanguageModelSession(instructions: task + " " + COMMON)
            let r = try await session.respond(to: text, options: GenerationOptions(temperature: mode == "correct" ? 0.2 : 0.7))
            emit(["text": r.content.trimmingCharacters(in: .whitespacesAndNewlines)])
        } catch {
            let d = "\(error)"
            let code = d.contains("exceededContextWindowSize") ? "too_long" : d.contains("guardrail") ? "refused" : d.contains("unsupportedLanguage") ? "language" : "failed"
            emit(["error": code, "msg": String(d.prefix(200))]); exit(1)
        }
    }
}
