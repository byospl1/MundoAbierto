// Envoltorio con identidad TCC propia para los LaunchAgents del Cerebro de
// Lecturas. macOS deniega en silencio el acceso a ~/Documents y a iCloud
// Drive a los binarios del sistema (bash, python3) lanzados por launchd,
// pero a un binario propio le muestra el dialogo de permiso UNA vez y
// persiste la concesion. Los procesos hijos heredan la responsabilidad TCC.
//
// Uso:      cerebro_agente /ruta/al/ejecutable [args...]
// Compilar: swiftc -O agente.swift -o cerebro_agente
// OJO: recompilarlo cambia su firma y macOS vuelve a pedir permiso.

import Foundation

let argumentos = Array(CommandLine.arguments.dropFirst())
guard let ejecutable = argumentos.first else {
    FileHandle.standardError.write(Data("uso: cerebro_agente <ejecutable> [args...]\n".utf8))
    exit(64)
}

let proceso = Process()
proceso.executableURL = URL(fileURLWithPath: ejecutable)
proceso.arguments = Array(argumentos.dropFirst())

do {
    try proceso.run()
} catch {
    FileHandle.standardError.write(Data("no pude lanzar \(ejecutable): \(error)\n".utf8))
    exit(126)
}
proceso.waitUntilExit()
exit(proceso.terminationStatus)
