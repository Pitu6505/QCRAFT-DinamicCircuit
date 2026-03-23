"""
Prueba local de compresion de topologia para todos los circuitos listados
(en comentarios o activos) en scheduler-async-compressed.py.

No ejecuta circuitos en nube. Solo:
1) Descarga el codigo de cada URL raw de GitHub
2) Convierte a QuantumCircuit de Qiskit
3) Aplica AdvancedTopologyCompressor en local
4) Reporta si comprimio qubits o no
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import aiohttp
import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister


CURRENT_DIR = Path(__file__).resolve().parent
SCHEDULER_DIR = CURRENT_DIR.parent
if str(SCHEDULER_DIR) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_DIR))

from circuit_Compresor import AdvancedTopologyCompressor

URL_PATTERN = re.compile(r"https://raw\.githubusercontent\.com/[^\s\"']+\.py")


def canonicalize_raw_github_url(url: str) -> str:
    parts = urlsplit(url)
    # Conserva el esquema+host y normaliza solo la ruta para quitar // repetidos.
    normalized_path = re.sub(r"/{2,}", "/", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, normalized_path, parts.query, parts.fragment))


@dataclass
class CircuitResult:
    url: str
    name: str
    status: str
    detail: str
    original_qubits: int = 0
    compressed_qubits: int = 0
    original_depth: int = 0
    compressed_depth: int = 0
    original_gates: int = 0
    compressed_gates: int = 0


def extract_urls_from_test_file(file_path: Path) -> List[str]:
    text = file_path.read_text(encoding="utf-8")
    seen = set()
    urls: List[str] = []
    for match in URL_PATTERN.findall(text):
        if match not in seen:
            urls.append(match)
            seen.add(match)
    return urls


async def extract_urls_from_github_tree(
    session: aiohttp.ClientSession,
    repo: str,
    branch: str,
    path_prefix: str,
    timeout_s: int,
) -> List[str]:
    api_url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    async with session.get(api_url, timeout=timeout) as response:
        response.raise_for_status()
        payload = json.loads(await response.text())

    tree = payload.get("tree", [])
    urls: List[str] = []
    for item in tree:
        path = item.get("path", "")
        item_type = item.get("type", "")
        if item_type != "blob":
            continue
        if not path.startswith(path_prefix):
            continue
        if not path.endswith(".py"):
            continue
        urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/{path}")

    urls.sort()
    return urls


def extract_circuit_name(url: str) -> str:
    return url.split("/")[-1]


def normalize_ibm_circuit_code(source: str) -> Tuple[str, int]:
    """
    Convierte un archivo de circuito Qiskit al formato minimo esperado para
    reconstruir un QuantumCircuit localmente, sin dependencias de cloud.
    """
    lines = source.splitlines()

    qreg_line = next(
        (line.split("#")[0].strip() for line in lines if "= QuantumRegister(" in line.split("#")[0]),
        None,
    )
    if qreg_line is None:
        raise ValueError("No se encontro QuantumRegister en el circuito")

    num_qubits = int(qreg_line.split("QuantumRegister(")[1].split(",")[0].strip())
    qreg_name = qreg_line.split("=")[0].strip()

    creg_line = next(
        (line.split("#")[0].strip() for line in lines if "= ClassicalRegister(" in line.split("#")[0]),
        None,
    )
    creg_name = creg_line.split("=")[0].strip() if creg_line else None

    circuit_name_line = next(
        (line.split("#")[0].strip() for line in lines if "= QuantumCircuit(" in line.split("#")[0]),
        None,
    )
    if circuit_name_line is None:
        raise ValueError("No se encontro asignacion de QuantumCircuit")
    file_circuit_name = circuit_name_line.split("=")[0].strip()

    circuit_lines = [
        line.split("#")[0].strip()
        for line in lines
        if line.split("#")[0].strip().startswith(file_circuit_name + ".") and "add_register" not in line
    ]

    if not circuit_lines:
        raise ValueError("No se encontraron operaciones sobre el circuito")

    code = "\n".join(circuit_lines)
    code = code.replace(file_circuit_name + ".", "circuit.")
    code = code.replace(f"{qreg_name}[", "qreg_q[")
    if creg_name:
        code = code.replace(f"{creg_name}[", "creg_c[")

    return code, num_qubits


def build_quantum_circuit_from_code(normalized_code: str, num_qubits: int) -> QuantumCircuit:
    qreg_q = QuantumRegister(num_qubits, "q")
    creg_c = ClassicalRegister(num_qubits, "c")
    circuit = QuantumCircuit(qreg_q, creg_c)

    namespace = {
        "circuit": circuit,
        "qreg_q": qreg_q,
        "creg_c": creg_c,
        "pi": 3.141592653589793,
        "np": np,
    }

    # Compatibilidad: muchos ejemplos antiguos usan "... .c_if(...)" encadenado.
    # En versiones recientes de Qiskit esa forma puede fallar (InstructionSet sin c_if).
    # Para este analisis de compresion local retiramos solo el condicionamiento clasico.
    safe_code = re.sub(r"\.c_if\s*\([^\)]*\)", "", normalized_code)

    # Ejecutamos solo lineas de operaciones normalizadas sobre 'circuit'.
    exec(safe_code, {"__builtins__": __builtins__}, namespace)

    return namespace["circuit"]


async def download_source(session: aiohttp.ClientSession, url: str, timeout_s: int) -> str:
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with session.get(url, timeout=timeout) as response:
        response.raise_for_status()
        return await response.text()


def analyze_one_circuit(source: str, url: str) -> CircuitResult:
    name = extract_circuit_name(url)

    if "qiskit" not in source:
        return CircuitResult(
            url=url,
            name=name,
            status="skipped",
            detail="No parece un circuito Qiskit",
        )

    try:
        compressor = AdvancedTopologyCompressor(coupling_map=None)
        normalized_code, num_qubits = normalize_ibm_circuit_code(source)
        qc = build_quantum_circuit_from_code(normalized_code, num_qubits)

        original_qubits = qc.num_qubits
        original_depth = qc.depth() or 0
        original_gates = len(qc.data)

        # El compresor imprime el circuito completo en stdout; lo silenciamos para
        # mantener la prueba legible cuando se procesan muchos circuitos.
        with contextlib.redirect_stdout(io.StringIO()):
            compressed_qc = compressor.compress_and_map(qc)
        if compressed_qc is None:
            return CircuitResult(
                url=url,
                name=name,
                status="error",
                detail="compress_and_map retorno None",
                original_qubits=original_qubits,
                original_depth=original_depth,
                original_gates=original_gates,
            )

        compressed_qubits = compressed_qc.num_qubits
        compressed_depth = compressed_qc.depth() or 0
        compressed_gates = len(compressed_qc.data)

        if compressed_qubits < original_qubits:
            status = "compressed"
            detail = f"{original_qubits}->{compressed_qubits} qubits"
        else:
            status = "not_compressed"
            detail = f"{original_qubits}->{compressed_qubits} qubits"

        return CircuitResult(
            url=url,
            name=name,
            status=status,
            detail=detail,
            original_qubits=original_qubits,
            compressed_qubits=compressed_qubits,
            original_depth=original_depth,
            compressed_depth=compressed_depth,
            original_gates=original_gates,
            compressed_gates=compressed_gates,
        )

    except Exception as exc:
        return CircuitResult(
            url=url,
            name=name,
            status="error",
            detail=str(exc)[:200],
        )


async def run_local_compression_test(
    source_file: Path,
    output_file: Path,
    timeout_s: int,
    concurrency: int,
    max_circuits: Optional[int],
    name_contains: Optional[str],
    url_mode: str,
    github_repo: str,
    github_branch: str,
    github_path_prefix: str,
) -> List[CircuitResult]:
    urls: List[str] = []

    async with aiohttp.ClientSession() as discovery_session:
        if url_mode in ("from-test-file", "both"):
            urls.extend(extract_urls_from_test_file(source_file))
        if url_mode in ("github-tree", "both"):
            urls.extend(
                await extract_urls_from_github_tree(
                    discovery_session,
                    repo=github_repo,
                    branch=github_branch,
                    path_prefix=github_path_prefix,
                    timeout_s=timeout_s,
                )
            )

    deduped = []
    seen = set()
    for url in urls:
        normalized_url = canonicalize_raw_github_url(url)
        if normalized_url not in seen:
            deduped.append(normalized_url)
            seen.add(normalized_url)
    urls = deduped

    if not urls:
        raise RuntimeError("No se encontraron URLs para procesar")

    if name_contains:
        needle = name_contains.lower()
        urls = [u for u in urls if needle in extract_circuit_name(u).lower()]
        if not urls:
            raise RuntimeError(f"No se encontraron circuitos que contengan '{name_contains}'")

    if max_circuits is not None:
        urls = urls[:max_circuits]

    print("=" * 90)
    print("LOCAL TOPOLOGY COMPRESSION TEST")
    print("=" * 90)
    print(f"Source file: {source_file}")
    print(f"URL mode: {url_mode}")
    print(f"GitHub repo: {github_repo}@{github_branch} [{github_path_prefix}]")
    print(f"Total URLs found: {len(urls)}")
    print(f"Output report: {output_file}")
    print()

    sem = asyncio.Semaphore(concurrency)
    results: List[CircuitResult] = []

    async with aiohttp.ClientSession() as session:

        async def worker(url: str) -> None:
            async with sem:
                name = extract_circuit_name(url)
                try:
                    source = await download_source(session, url, timeout_s)
                    result = analyze_one_circuit(source, url)
                except Exception as exc:
                    result = CircuitResult(
                        url=url,
                        name=name,
                        status="error",
                        detail=f"download/analyze error: {str(exc)[:160]}",
                    )

                results.append(result)
                print(f"[{len(results):03d}/{len(urls):03d}] {result.status:14s} {result.name} - {result.detail}")

        await asyncio.gather(*(worker(url) for url in urls))

    results.sort(key=lambda r: (r.status, r.name))
    write_report(output_file, results, source_file)
    return results


def write_report(output_file: Path, results: List[CircuitResult], source_file: Path) -> None:
    compressed = [r for r in results if r.status == "compressed"]
    not_compressed = [r for r in results if r.status == "not_compressed"]
    skipped = [r for r in results if r.status == "skipped"]
    errors = [r for r in results if r.status == "error"]

    lines: List[str] = []
    lines.append("=" * 90)
    lines.append("REPORTE LOCAL: TOPOLOGY COMPRESSION")
    lines.append("=" * 90)
    lines.append(f"Archivo origen URLs: {source_file}")
    lines.append(f"Total circuitos analizados: {len(results)}")
    lines.append(f"Comprimidos: {len(compressed)}")
    lines.append(f"No comprimidos: {len(not_compressed)}")
    lines.append(f"Saltados: {len(skipped)}")
    lines.append(f"Errores: {len(errors)}")
    lines.append("")

    lines.append("--- COMPRIMIDOS ---")
    for r in compressed:
        lines.append(
            f"{r.name} | Q {r.original_qubits}->{r.compressed_qubits} | "
            f"D {r.original_depth}->{r.compressed_depth} | "
            f"G {r.original_gates}->{r.compressed_gates} | {r.url}"
        )

    lines.append("")
    lines.append("--- NO COMPRIMIDOS ---")
    for r in not_compressed:
        lines.append(
            f"{r.name} | Q {r.original_qubits}->{r.compressed_qubits} | "
            f"D {r.original_depth}->{r.compressed_depth} | "
            f"G {r.original_gates}->{r.compressed_gates} | {r.url}"
        )

    lines.append("")
    lines.append("--- SALTADOS ---")
    for r in skipped:
        lines.append(f"{r.name} | {r.detail} | {r.url}")

    lines.append("")
    lines.append("--- ERRORES ---")
    for r in errors:
        lines.append(f"{r.name} | {r.detail} | {r.url}")

    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    default_source = Path(__file__).with_name("scheduler-async-compressed.py")
    default_output = Path(__file__).with_name("SalidaTopologyCompressedLocal.txt")

    parser = argparse.ArgumentParser(
        description="Prueba local de compresion para todos los circuitos de scheduler-async-compressed.py"
    )
    parser.add_argument("--source-file", type=Path, default=default_source, help="Archivo desde el que leer URLs")
    parser.add_argument("--output-file", type=Path, default=default_output, help="Archivo de salida del reporte")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout de descarga por URL")
    parser.add_argument("--concurrency", type=int, default=8, help="Numero maximo de descargas simultaneas")
    parser.add_argument("--max-circuits", type=int, default=None, help="Limita el numero de circuitos a procesar")
    parser.add_argument(
        "--name-contains",
        type=str,
        default=None,
        help="Filtra circuitos por substring en el nombre del archivo",
    )
    parser.add_argument(
        "--url-mode",
        type=str,
        choices=["from-test-file", "github-tree", "both"],
        default="both",
        help="Origen de URLs: archivo de test, discovery GitHub o ambos",
    )
    parser.add_argument("--github-repo", type=str, default="Qcraft-UEx/QCRAFT-Scheduler", help="owner/repo en GitHub")
    parser.add_argument("--github-branch", type=str, default="main", help="Rama para discovery GitHub")
    parser.add_argument(
        "--github-path-prefix",
        type=str,
        default="circuits-code/",
        help="Prefijo de ruta dentro del repo para filtrar circuitos",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = time.time()

    results = asyncio.run(
        run_local_compression_test(
            source_file=args.source_file,
            output_file=args.output_file,
            timeout_s=args.timeout,
            concurrency=args.concurrency,
            max_circuits=args.max_circuits,
            name_contains=args.name_contains,
            url_mode=args.url_mode,
            github_repo=args.github_repo,
            github_branch=args.github_branch,
            github_path_prefix=args.github_path_prefix,
        )
    )

    compressed = sum(1 for r in results if r.status == "compressed")
    not_compressed = sum(1 for r in results if r.status == "not_compressed")
    skipped = sum(1 for r in results if r.status == "skipped")
    errors = sum(1 for r in results if r.status == "error")

    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(f"Compressed:      {compressed}")
    print(f"Not compressed:  {not_compressed}")
    print(f"Skipped:         {skipped}")
    print(f"Errors:          {errors}")
    print(f"Total:           {len(results)}")
    print(f"Elapsed seconds: {time.time() - start:.2f}")


if __name__ == "__main__":
    main()
