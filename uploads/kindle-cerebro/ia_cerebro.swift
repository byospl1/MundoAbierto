// Puente persistente al modelo local de Apple Intelligence (FoundationModels,
// macOS 26+) para el Cerebro de Lecturas. Adaptado de pdf_a_epub/ia_local.swift.
//
// Servidor JSONL: una peticion {"id":N,"prompt":"..."} por linea de stdin,
// una respuesta {"id":N,"ok":true,"texto":"..."} por linea de stdout.
// El modelo se carga UNA vez por proceso; sesion nueva por peticion
// (contexto limpio: 4096 tokens totales compartidos entrada+salida).
//
// Compilar:  swiftc -O -parse-as-library ia_cerebro.swift -o ia_cerebro
// Salida: 0 fin normal, 2 modelo no disponible.

import Foundation
import FoundationModels

struct Peticion: Codable {
    let id: Int
    let prompt: String
}

struct Respuesta: Codable {
    let id: Int
    let ok: Bool
    var texto: String? = nil
    var error: String? = nil
}

func emitir(_ r: Respuesta) {
    let datos = (try? JSONEncoder().encode(r)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(datos)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

@main
struct IACerebro {
    static func main() async {
        let modelo = SystemLanguageModel(
            useCase: .general,
            guardrails: .permissiveContentTransformations
        )
        guard case .available = modelo.availability else {
            FileHandle.standardError.write(
                "NO_DISPONIBLE: \(modelo.availability)\n".data(using: .utf8)!)
            exit(2)
        }

        let instrucciones = Instructions(
            "Eres un asistente de analisis de lecturas y libros. Respondes "
            + "UNICAMENTE con el JSON que se te pide, sin explicaciones ni "
            + "texto adicional. Siempre en espanol.")

        LanguageModelSession(model: modelo, instructions: instrucciones).prewarm()
        emitir(Respuesta(id: 0, ok: true, texto: "listo"))

        let opciones = GenerationOptions(sampling: .greedy,
                                         maximumResponseTokens: 900)

        while let linea = readLine(strippingNewline: true) {
            guard !linea.isEmpty else { continue }
            guard let peticion = try? JSONDecoder().decode(
                Peticion.self, from: Data(linea.utf8)) else {
                emitir(Respuesta(id: -1, ok: false, error: "JSON invalido"))
                continue
            }
            let sesion = LanguageModelSession(model: modelo,
                                              instructions: instrucciones)
            do {
                let r = try await sesion.respond(to: peticion.prompt,
                                                 options: opciones)
                emitir(Respuesta(id: peticion.id, ok: true, texto: r.content))
            } catch {
                emitir(Respuesta(id: peticion.id, ok: false,
                                 error: "\(error)"))
            }
        }
    }
}
