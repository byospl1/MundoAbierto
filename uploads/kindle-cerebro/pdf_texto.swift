// Extrae el texto de un PDF usando PDFKit nativo de macOS (sin dependencias).
// Uso:      pdf_texto /ruta/al/archivo.pdf
// Imprime el texto en stdout; primera linea a stderr: "PAGINAS n".
// Salida: 0 ok · 1 no se pudo abrir · 2 PDF sin texto (probablemente escaneado)

import Foundation
import PDFKit

let args = CommandLine.arguments
guard args.count > 1,
      let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write(Data("NO_ABRE\n".utf8))
    exit(1)
}

var texto = doc.string ?? ""
// PDFKit a veces devuelve "" en el string global pero si por pagina
if texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
    var partes: [String] = []
    for i in 0..<doc.pageCount {
        if let p = doc.page(at: i)?.string { partes.append(p) }
    }
    texto = partes.joined(separator: "\n")
}

FileHandle.standardError.write(Data("PAGINAS \(doc.pageCount)\n".utf8))
if texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
    FileHandle.standardError.write(Data("SIN_TEXTO\n".utf8))
    exit(2)
}
FileHandle.standardOutput.write(Data(texto.utf8))
