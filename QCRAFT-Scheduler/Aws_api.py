# Aws_api.py
from qiskit_braket_provider import AWSBraketProvider

def get_aws_backend_data(target_machine="Ankaa-3"):
    """
    Se conecta a AWS Braket y obtiene la topología de la máquina seleccionada
    buscando coincidencias parciales en el nombre.
    """
    print(f"🛰️ Conectando a AWS Braket buscando la instancia '{target_machine}'...")
    try:
        # El provider lee automáticamente tus credenciales y región del sistema
        provider = AWSBraketProvider()
        
        # 1. Obtenemos TODAS las máquinas disponibles
        backends = provider.backends()
        selected_backend = None
        
        # 2. Buscamos de forma flexible (ignorando mayúsculas)
        for b in backends:
            if target_machine.lower() in b.name.lower():
                selected_backend = b
                break
                
        # 3. Si no la encuentra, te avisamos y te mostramos qué opciones hay
        if not selected_backend:
            print(f"❌ No se encontró ninguna máquina que contenga '{target_machine}'.")
            print(f"💡 Máquinas disponibles en tu región actual: {[b.name for b in backends]}")
            return None, None

        print(f"✅ Máquina AWS encontrada: {selected_backend.name}")
        
        # 4. Extraemos el CouplingMap
        coupling_map = selected_backend.coupling_map
        
        if coupling_map is None:
            print(f"   -> Nota: {selected_backend.name} tiene conectividad total/All-to-All.")
        else:
            print(f"   -> Topología física obtenida correctamente.")
            
        return coupling_map, selected_backend

    except Exception as e:
        print(f"❌ Error crítico al conectar con AWS Braket: {e}")
        return None, None