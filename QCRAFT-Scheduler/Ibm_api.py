# Ibm_api.py
from qiskit_ibm_runtime import QiskitRuntimeService

def get_backend_data(api_key, instance_crn, backend_name="ibm_fez"):
    """
    Se conecta a IBM Cloud, recupera la topología y los datos de calibración.
    """
    print(f"Conectando a IBM Cloud para la instancia {backend_name}...")
    try:
        service = QiskitRuntimeService(
            channel="ibm_cloud",
            token=api_key,
            instance=instance_crn
        )
        backend = service.backend(backend_name)
        
        # Obtenemos el CouplingMap directo (ideal para nuestro transpilador)
        coupling_map = backend.coupling_map
        
        # Obtenemos las propiedades para tu algoritmo de la Mochila (ruido, calibración)
        properties = backend.properties()
        qubit_props = properties.to_dict()
        gate_props = properties.gates
        
        # Mostrar fecha de última calibración
        last_update = properties.last_update_date
        print(f"✅ Topología obtenida.")
        print(f"📅 Última calibración de {backend_name}: {last_update}")
        
        return coupling_map, qubit_props, gate_props, backend

    except Exception as e:
        print(f"❌ Error al conectar con IBM Cloud: {e}")
        return None, None, None, None