"""
Test asíncrono para la política Compressed_ML_Knapsack
Envía múltiples circuitos que se comprimen individualmente antes de aplicar mochila ML
"""
import aiohttp
import asyncio
import time

url = 'http://localhost:8084/'

# Conjunto reducido de circuitos para testing
urls = {
    "Popular-dj-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/Deutsch-Jozsa/Deutsch-Jozsa_qcraft.py",
    "Popular-dj-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/Deutsch-Jozsa/dj_indep_14_mqt.py",
    "Popular-dj-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/Deutsch-Jozsa/dj_indep_7_mqt.py",
    "Popular-adder-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/adder/adder_n10_vq.py",
    "Popular-adder-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/adder/adder_n7_vq.py",
    "Popular-grover-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/grover/grover-noancilla_3_mqt.py",
    "Popular-grover-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/grover/grover-v-chain_3_mqt.py",
    "Popular-grover-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/grover/grover-v-chain_4_mqt.py",
    "Popular-grover-4": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/grover/grover_7_vq.py",
    "Popular-pe-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/phase_estimation/pe_6_mqt.py",
    "Popular-pe-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/phase_estimation/phase_estimation_qcraft.py",
    "Popular-qft-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/qft/qft_3_vq.py",
    "Popular-qft-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/qft/qft_6_mqt.py",
    "Popular-qft-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/qft/qft_7_vq.py",
    "Popular-qft-4": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/qft/qft_qcraft.py",
    "Popular-shor-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/shor/shor_mod15_mqt.py",
    "Popular-shor-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/shor/shor_mod21_mqt.py",
    "Popular-shor-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/shor/shor_qcraft.py",
    "Popular-simon-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/simon/simon_vq.py",
    "Popular-tsp-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/tsp/tsp_indep_4_mqt.py",
    "Popular-tsp-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/tsp/tsp_indep_5_mqt.py",
    "Popular-tsp-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/popularalgorithms/tsp/tsp_qcraft.py",
    "Reversible-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/reversible/3_17tc_vq.py",
    "efficient-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/EfficientSU2/su2random_4_mqt.py",
    "qaoa-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/qaoa/qaoa_indep_4_mqt.py",
    "qaoa-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/qaoa/qaoa_indep_6_mqt.py",
    "qaoa-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/qaoa/qaoa_qcraft.py",
    "vqe-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/vqe/vqe_indep_3_mqt.py",
    "vqe-2": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/vqe/vqe_indep_4_mqt.py",
    "vqe-3": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/vqe/vqe_indep_5_mqt.py",
    "vqe-4": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//variational/vqe/vqe_indep_6_mqt.py",
    "Mapping-1": "https://raw.githubusercontent.com/Qcraft-UEx/QCRAFT-Scheduler/main/circuits-code//combinational/mapping/20QBT_8CYC_16GN_1.0P2_0_vq.py",
}

async def send_circuit(session, circuit_url, circuit_name):
    """Envía un circuito al scheduler"""
    data_template = {
        "circuit": circuit_url,
        "num_qubits": 0,  # El scheduler lo calculará
        "shots": 10000,
        "user": "127.0.0.1",  # IP única para rastreo
        "circuit_name": circuit_name,
        "maxDepth": 0,  # El scheduler lo calculará
        "provider": "ibm",  # Solo IBM para testing
        "policy": "Compressed_ML_Knapsack",  # ← POLÍTICA NUEVA
        "Iteracion": 1
    }
    
    try:
        async with session.post(url + 'service/Compressed_ML_Knapsack', json=data_template) as response:
            result = await response.text()
            if response.status == 200:
                return {'success': True, 'circuit': circuit_name, 'url': circuit_url}
            else:
                return {'success': False, 'circuit': circuit_name, 'url': circuit_url, 'error': f'Status {response.status}'}
    except Exception as e:
        return {'success': False, 'circuit': circuit_name, 'url': circuit_url, 'error': str(e)}

async def main():
    """Función principal que envía todos los circuitos"""
    print("=" * 80)
    print("🗜️🤖 TEST ASÍNCRONO: Política Compressed_ML_Knapsack")
    print("=" * 80)
    print()
    print("📊 Configuración:")
    print(f"   - Total de circuitos: {len(urls)}")
    print(f"   - Policy: Compressed_ML_Knapsack")
    print(f"   - Provider: IBM")
    print(f"   - Shots: 10000")
    print(f"   - URL del scheduler: {url}")
    print()
    
    print("🚀 Enviando circuitos al scheduler...")
    print()
    
    async with aiohttp.ClientSession() as session:
        tasks = [send_circuit(session, circuit_url, name) for name, circuit_url in urls.items()]
        
        print(f"⏳ Esperando respuestas de {len(urls)} circuitos...")
        print()
        results = await asyncio.gather(*tasks)
    
    # Mostrar resultados
    print()
    print("=" * 80)
    print("📋 RESULTADOS:")
    print("=" * 80)
    
    successful = 0
    failed = 0
    
    for i, result in enumerate(results, 1):
        if result['success']:
            print(f"  {i}. ✓ {result['circuit']}")
            successful += 1
        else:
            print(f"  {i}. ✗ Error en {result['url']}: {result['error']}")
            failed += 1
    
    print()
    print("=" * 80)
    print("📈 ESTADÍSTICAS:")
    print(f"   ✅ Exitosos: {successful}/{len(urls)}")
    print(f"   ❌ Fallidos: {failed}/{len(urls)}")
    print(f"   📊 Tasa de éxito: {successful/len(urls)*100:.1f}%")
    print("=" * 80)
    print()
    print("💡 Tip: Revisa los logs del scheduler para ver:")
    print("   1. Compresión individual de cada circuito")
    print("   2. Selección por mochila ML con tamaños comprimidos")
    print("   3. Ejecución sin re-compresión")
    print("   📄 Archivo de métricas: SalidaCompressedML.txt")

if __name__ == '__main__':
    asyncio.run(main())
