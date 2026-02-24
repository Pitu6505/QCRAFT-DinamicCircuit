# main.py
from qiskit import QuantumCircuit, transpile
from circuit_Compresor import AdvancedTopologyCompressor

# Importamos las APIs de ambos mundos
from Ibm_api import get_backend_data as get_ibm_data
from Aws_api import get_aws_backend_data
from config import IBM_API_KEY, IBM_INSTANCE_CRN

PROVEEDOR_ELEGIDO = "AWS"  # Cambia entre "IBM" y "AWS"

print(f"\n=== INICIANDO PIPELINE QCaaS PARA {PROVEEDOR_ELEGIDO} ===")

# 1. Obtenemos el mapa físico según el proveedor
if PROVEEDOR_ELEGIDO == "IBM":
    cmap, _, _, backend = get_ibm_data(IBM_API_KEY, IBM_INSTANCE_CRN, "ibm_fez")
elif PROVEEDOR_ELEGIDO == "AWS":
    cmap, backend = get_aws_backend_data("Ankaa-3") # Puedes probar "IonQ Aria 1"

# 2. Tu circuito de prueba (ej. el de Simon que vimos antes)
qc = QuantumCircuit(4, 4)
qc.h(0)
qc.cx(0, 1)
qc.barrier()
qc.cx(1, 2)
qc.measure([0, 1, 2], [0, 1, 2])

if backend is not None:
    # 3. Limpieza Lógica (Agnóstica)
    circuito_limpio = transpile(qc, optimization_level=3)
    
    # 4. Compresión y Mapeo Físico
    # Le pasamos la topología que descargamos, da igual si es de IBM o Rigetti
    compressor = AdvancedTopologyCompressor(coupling_map=cmap)
    circuito_final_optimizado = compressor.compress_and_map(circuito_limpio)
    
    print("\n=== CIRCUITO FINAL LISTO PARA LA MOCHILA ===")
    print(circuito_final_optimizado)
    
    # A partir de aquí, se lo pasas a tu mochila. 
    # Para ejecutarlo realmente en AWS después de tu mochila, usarías:
    # backend.run(circuito_multiplexado_de_la_mochila, shots=1000)