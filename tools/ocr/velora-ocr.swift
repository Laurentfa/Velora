// Lecture de texte sur une image (JPEG, PNG…) avec le framework Vision d'Apple : tout se passe sur l'ordinateur, rien n'est envoyé.
// Usage : velora-ocr <image>   -> le texte lu sur la sortie standard, une ligne par ligne de texte, de haut en bas. Code de sortie : 0 (même sans texte), 2 (image illisible), 3 (lecture impossible).
import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count >= 2, let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("image illisible\n".data(using: .utf8)!)
    exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["fr-FR", "en-US"]
do {
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
} catch {
    FileHandle.standardError.write("lecture impossible\n".data(using: .utf8)!)
    exit(3)
}
let lines = (request.results ?? []).compactMap { obs -> (CGFloat, CGFloat, String)? in
    guard let top = obs.topCandidates(1).first else { return nil }
    return (obs.boundingBox.maxY, obs.boundingBox.minX, top.string)
}
// de haut en bas ; deux blocs presque à la même hauteur se lisent de gauche à droite
let sorted = lines.sorted { abs($0.0 - $1.0) > 0.012 ? $0.0 > $1.0 : $0.1 < $1.1 }
print(sorted.map { $0.2 }.joined(separator: "\n"))
