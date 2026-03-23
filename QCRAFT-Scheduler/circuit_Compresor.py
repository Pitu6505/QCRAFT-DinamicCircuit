# circuit_Compresor.py
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister, transpile
from qiskit.transpiler import CouplingMap
from collections import defaultdict

class AdvancedTopologyCompressor:
    """
    Compresor Dinámico que adelanta medidas, reutiliza qubits y mapea 
    el resultado a la topología física de una máquina real.
    
    IMPORTANTE: Detecta y preserva el entanglement entre qubits.
    Qubits entangled NO pueden compartir el mismo qubit físico.
    """
    def __init__(self, coupling_map=None):
        self.coupling_map = coupling_map

    def _advance_measurements(self, circuit):
        last_gate_idx = {q: -1 for q in circuit.qubits}
        measures = {} 
        
        # 1. Analizamos dónde está la última puerta de cada qubit
        for i, inst in enumerate(circuit.data):
            if inst.operation.name == 'measure':
                measures[inst.qubits[0]] = inst.clbits[0]
            elif inst.operation.name != 'barrier': 
                for q in inst.qubits:
                    last_gate_idx[q] = i
                    
        # Creamos el circuito en blanco
        new_qc = QuantumCircuit(*circuit.qregs, *circuit.cregs)
        measured_qubits = set()
        
        # Si un qubit se mide pero nunca tuvo puertas lógicas (last_gate_idx == -1),
        # lo medimos ahora mismo al principio del todo para no arrastrarlo.
        for q, c in measures.items():
            if last_gate_idx[q] == -1:
                new_qc.measure(q, c)
                measured_qubits.add(q)
        
        # 2. Reconstruimos el circuito
        for i, inst in enumerate(circuit.data):
            if inst.operation.name == 'measure':
                continue 
                
            new_qc.append(inst.operation, inst.qubits, inst.clbits)
            
            # Revisar si acabamos de añadir la ÚLTIMA puerta útil de algún qubit
            for q in inst.qubits:
                if last_gate_idx[q] == i and q in measures and q not in measured_qubits:
                    new_qc.measure(q, measures[q])
                    measured_qubits.add(q)
                    
        # 3. Por seguridad, si quedó alguna medida rezagada, la ponemos al final
        for q, c in measures.items():
            if q not in measured_qubits:
                new_qc.measure(q, c)
                measured_qubits.add(q)
                
        return new_qc

    def _get_qubit_lifetimes(self, circuit):
        qubit_lifetimes = {q: {'start': float('inf'), 'end': -1} for q in circuit.qubits}
        qubit_current_time = {q: 0 for q in circuit.qubits}
        
        for inst in circuit.data:
            qargs = inst.qubits
            if not qargs:
                continue
                
            start_time = max(qubit_current_time[q] for q in qargs)
            
            if inst.operation.name == 'barrier':
                for q in qargs:
                    qubit_current_time[q] = start_time
                continue
                
            end_time = start_time + 1
            
            for q in qargs:
                if qubit_lifetimes[q]['start'] == float('inf'):
                    qubit_lifetimes[q]['start'] = start_time
                qubit_lifetimes[q]['end'] = end_time
                qubit_current_time[q] = end_time
                
        return qubit_lifetimes

    def _build_entanglement_graph(self, circuit):
        """
        Construye un grafo de entanglement entre qubits.
        Si dos qubits han interactuado mediante un gate multi-qubit,
        están entangled y NO pueden compartir el mismo qubit físico.
        
        Returns:
            dict: {qubit: set(qubits_entangled_with_it)}
        """
        entanglement_graph = defaultdict(set)
        
        for inst in circuit.data:
            qargs = inst.qubits
            
            # Ignorar barriers y medidas (no crean entanglement)
            if inst.operation.name in ['barrier', 'measure']:
                continue
            
            # Si el gate actúa sobre 2+ qubits, crea entanglement
            if len(qargs) >= 2:
                # Todos los qubits involucrados están entangled entre sí
                for i, q1 in enumerate(qargs):
                    for q2 in qargs[i+1:]:
                        entanglement_graph[q1].add(q2)
                        entanglement_graph[q2].add(q1)
                        
        return entanglement_graph

    def _can_reuse_physical_lane(self, logical_q, lane_idx, physical_lanes_info, 
                                  entanglement_graph, lifetimes):
        """
        Verifica si un qubit lógico puede reutilizar un carril físico.
        
        NO puede reutilizar si:
        1. El carril aún está en uso (tiempo)
        2. Está entangled con algún qubit que ya usa ese mismo carril Y se superponen en tiempo
        
        Args:
            logical_q: Qubit lógico a asignar
            lane_idx: Índice del carril físico candidato
            physical_lanes_info: Lista de dict con info de cada carril
            entanglement_graph: Grafo de entanglement
            lifetimes: Tiempos de vida de cada qubit
            
        Returns:
            bool: True si puede reutilizar, False si no
        """
        lane_info = physical_lanes_info[lane_idx]
        lane_end_time = lane_info['end_time']
        logical_q_start = lifetimes[logical_q]['start']
        logical_q_end = lifetimes[logical_q]['end']
        
        # 1. Verificar tiempo: el carril debe estar libre
        if lane_end_time > logical_q_start:
            return False
        
        # 2. Verificar entanglement: no debe estar entangled con qubits que usan este carril
        #    y que se superponen en tiempo
        entangled_qubits = entanglement_graph.get(logical_q, set())
        
        for other_logical_q in lane_info['qubits_history']:
            # Si logical_q está entangled con other_logical_q
            if other_logical_q in entangled_qubits:
                # Verificar si se superponen en tiempo
                other_start = lifetimes[other_logical_q]['start']
                other_end = lifetimes[other_logical_q]['end']
                
                # Verificar superposición temporal
                # Se superponen si: not (logical_q termina antes que other empiece O other termina antes que logical empiece)
                overlaps = not (logical_q_end <= other_start or other_end <= logical_q_start)
                
                if overlaps:
                    # Están entangled Y se superponen → NO puede reutilizar
                    return False
        
        return True

    def _rebuild_circuit(self, original_circuit, mapping, resets, num_phys):
        qr_phys = QuantumRegister(num_phys, 'q_phys')
        new_qc = QuantumCircuit(qr_phys)
        for creg in original_circuit.cregs:
            new_qc.add_register(creg)

        reset_applied = set()

        for inst in original_circuit.data:
            if inst.operation.name == 'barrier':
                continue
                
            new_qargs = []
            for q in inst.qubits:
                phys_idx = mapping[q]
                
                if resets[q] and q not in reset_applied:
                    new_qc.reset(qr_phys[phys_idx])
                    reset_applied.add(q)
                    
                new_qargs.append(qr_phys[phys_idx])
            
            if new_qargs:
                new_qc.append(inst.operation, new_qargs, inst.clbits)
                
        return new_qc

    def compress_and_map(self, circuit):
        """
        Comprime el circuito y lo mapea a la topología física.
        
        Args:
            circuit: QuantumCircuit de Qiskit
            
        Returns:
            QuantumCircuit comprimido (o el original si falla la compresión)
        """
        try:
            # Validar que el circuito no sea None
            if circuit is None:
                print("-> ❌ Error: Circuito es None, no se puede comprimir")
                return None
            
            print(f"-> Circuito Original: {circuit.num_qubits} qubits lógicos.")
            print("\n Circuito original:")
            print(circuit)
            qc_early = self._advance_measurements(circuit)
            
            if qc_early is None:
                print("-> ⚠️ Advertencia: _advance_measurements retornó None, usando circuito original")
                return circuit
            
            # PASO 1: Obtener tiempos de vida
            lifetimes = self._get_qubit_lifetimes(qc_early)
            
            # PASO 2: Construir grafo de entanglement
            entanglement_graph = self._build_entanglement_graph(qc_early)
            
            # DEBUG: Mostrar entanglement detectado
            if entanglement_graph:
                print(f"-> 🔗 Entanglement detectado:")
                for q, entangled_with in entanglement_graph.items():
                    if entangled_with:
                        q_idx = circuit.qubits.index(q) if q in circuit.qubits else '?'
                        entangled_indices = [circuit.qubits.index(eq) for eq in entangled_with if eq in circuit.qubits]
                        print(f"   Q[{q_idx}] entangled con Q{entangled_indices}")
            else:
                print(f"-> ℹ️ No se detectó entanglement (circuito puede comprimirse más)")
            
            # PASO 3: Asignar qubits lógicos a carriles físicos (respetando entanglement)
            sorted_qubits = sorted(lifetimes.keys(), key=lambda q: lifetimes[q]['start'])
            
            # Estructura mejorada: cada carril tiene end_time e historial de qubits
            physical_lanes_info = []  # [{end_time: int, qubits_history: [qubits]}]
            logical_to_physical_map = {}
            resets_needed = {}

            for logical_q in sorted_qubits:
                start = lifetimes[logical_q]['start']
                end = lifetimes[logical_q]['end']
                if start == float('inf'): 
                    continue
                    
                assigned = False
                
                # Intentar reutilizar un carril existente
                for lane_idx in range(len(physical_lanes_info)):
                    if self._can_reuse_physical_lane(logical_q, lane_idx, physical_lanes_info, 
                                                     entanglement_graph, lifetimes):
                        # Puede reutilizar este carril
                        physical_lanes_info[lane_idx]['end_time'] = end
                        physical_lanes_info[lane_idx]['qubits_history'].append(logical_q)
                        logical_to_physical_map[logical_q] = lane_idx
                        resets_needed[logical_q] = True
                        assigned = True
                        break
                
                # Si no pudo reutilizar, crear nuevo carril físico
                if not assigned:
                    new_lane_idx = len(physical_lanes_info)
                    physical_lanes_info.append({
                        'end_time': end,
                        'qubits_history': [logical_q]
                    })
                    logical_to_physical_map[logical_q] = new_lane_idx
                    resets_needed[logical_q] = False

            num_phys = len(physical_lanes_info)
            
            # Si no se pudo comprimir (misma cantidad de qubits), retornar original
            if num_phys == 0 or num_phys >= circuit.num_qubits:
                print(f"-> ⚠️ No se logró compresión efectiva ({circuit.num_qubits} → {num_phys}), usando original")
                return circuit
                
            qc_compressed = self._rebuild_circuit(qc_early, logical_to_physical_map, resets_needed, num_phys)
            
            if qc_compressed is None:
                print("-> ⚠️ Advertencia: _rebuild_circuit retornó None, usando circuito original")
                return circuit
                
            print(f"-> Compresión completada: de {circuit.num_qubits} a {num_phys} qubits físicos.")
            
            if self.coupling_map:
                print("-> Adaptando a la topología física (Routing con subgrafo limitado)...")
                try:
                    # Crear coupling_map reducido: solo qubits 0 a num_phys-1
                    # Esto IMPIDE que SABRE expanda más allá de los qubits comprimidos
                    edges = self.coupling_map.get_edges()
                    reduced_edges = []
                    for edge in edges:
                        # Solo incluir conexiones dentro del rango [0, num_phys-1]
                        if edge[0] < num_phys and edge[1] < num_phys:
                            reduced_edges.append(edge)
                    
                    # Crear CouplingMap reducido
                    reduced_coupling = CouplingMap(reduced_edges)
                    
                    print(f"   └─ Coupling reducido: {len(edges)} aristas → {len(reduced_edges)} aristas ({num_phys} qubits)")
                    
                    # Layout inicial: qubits contiguos
                    initial_layout = list(range(num_phys))
                    
                    qc_mapped = transpile(
                        qc_compressed,
                        coupling_map=reduced_coupling,   # ← CLAVE: Solo qubits 0..num_phys-1
                        initial_layout=initial_layout,
                        optimization_level=2,
                        routing_method='sabre',
                        layout_method='dense'
                    )
                    
                    if qc_mapped is not None:
                        print(f"-> Routing completado: {qc_compressed.num_qubits} → {qc_mapped.num_qubits} qubits")
                        
                        # Verificar expansión (ahora DEBE ser <= num_phys por diseño)
                        if qc_mapped.num_qubits > num_phys:
                            print(f"-> ⚠️ IMPOSIBLE: Routing expandió con coupling reducido ({num_phys} → {qc_mapped.num_qubits})")
                            print(f"   Esto indica un bug - reportar. Usando sin routing.")
                            return qc_compressed
                        
                        print(f"   ✅ Routing exitoso sin expansión ({qc_mapped.num_qubits} qubits)")
                        print("\n Circuito mapeado a topología física:")
                        print(qc_mapped)
                        return qc_mapped
                    else:
            
                        print("-> ⚠️ Transpile retornó None, usando circuito comprimido sin routing")
                        return qc_compressed
                        
                except Exception as e:
                    print(f"-> ⚠️ Error en routing: {e}")
                    print("   Usando circuito comprimido sin routing")
                    print("\n Circuito comprimido sin routing:")
                    print(qc_compressed)
                    return qc_compressed
            else:
                return qc_compressed
                
        except Exception as e:
            print(f"-> ❌ Error fatal en compress_and_map: {e}")
            print("-> Retornando circuito original sin comprimir")
            import traceback
            traceback.print_exc()
            return circuit  # Siempre retornar el circuito original si algo falla