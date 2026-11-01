"""Private pipe to an optional MLIP Python; the GUI keeps its own dependencies."""

import json
from pathlib import Path
import pickle
import subprocess
import sys
from threading import Lock
import weakref

import numpy as np
from ase import Atoms
from ase.calculators.calculator import Calculator, all_changes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vbuild.mlip import MODELS
from vbuild.runtime import NO_WINDOW, process_env
from vbuild.paths import work_dir
from vbuild.devices import check_device, gpu_device


def load(model, path, device, charge_spin=(0, 1)):
    item = MODELS[model]
    check_device(item.engine, device)
    if item.engine == 'nep':
        from vbuild.nep import calculator
        return calculator(path, device)
    if item.engine == 'deepmd':
        if device != 'cpu':
            hardware('deepmd')
        from vbuild.dpa import DpaCalculator
        return DpaCalculator(path, device, charge_spin)
    import torch
    torch.set_num_threads(4)
    from mace.calculators import MACECalculator
    options = {} if item.head is None else {'head': item.head}
    calculator = MACECalculator(model_paths=str(path), device=device, default_dtype='float32', **options)
    if item.head is not None and item.head not in calculator.available_heads:
        raise ValueError('模型不包含指定 head：' + item.head)
    return calculator


def hardware(engine):
    device = gpu_device()
    check_device(engine, device)
    if engine == 'nep':
        from vbuild.nep import prepare_backend
        prepare_backend()
        from nep_adapters import backend_status
        result = backend_status('cuda')
        if not result.available:
            raise RuntimeError(str(result))
        return result.devices[0].name
    import torch
    # Run an actual GPU operation, not just a driver-name query.
    (torch.ones(1, device=device, dtype=torch.float32) * 2).cpu()
    if device == 'mps':
        result = subprocess.run(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True, check=True)
        return result.stdout.strip() + ' GPU (MPS)'
    return torch.cuda.get_device_name(0)


def close_process(process, log):
    if process.poll() is None:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    process.stdout.close()
    log.close()


class RemoteCalculator(Calculator):
    implemented_properties = ['energy', 'forces', 'stress']

    def __init__(self, python, model, path, device, charge_spin=(0, 1)):
        super().__init__()
        logs = work_dir() / 'log'
        logs.mkdir(parents=True, exist_ok=True)
        log = (logs / ('engine-' + model + '.log')).open('ab', buffering=0)
        self.process = subprocess.Popen([str(python), '-B', '-u', str(Path(__file__)), 'serve', model, str(path), device,
                                         *map(str, charge_spin)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=process_env(python), creationflags=NO_WINDOW)
        self.finalizer = weakref.finalize(self, close_process, self.process, log)
        self.lock = Lock()
        self.receive()

    def receive(self):
        try:
            message = pickle.load(self.process.stdout)
        except (EOFError, OSError) as error:
            raise RuntimeError('计算 Python 已退出；请查看 work/log/engine-*.log。') from error
        if 'error' in message:
            raise RuntimeError(message['error'])
        return message

    def calculate(self, atoms=None, properties=('energy', 'forces'), system_changes=all_changes):
        super().calculate(atoms, properties, system_changes)
        with self.lock:
            pickle.dump(dict(symbols=atoms.get_chemical_symbols(), positions=atoms.positions,
                             cell=atoms.cell.array, pbc=atoms.pbc, properties=properties), self.process.stdin, protocol=4)
            self.process.stdin.flush()
            self.results = self.receive()['results']

    def close(self):
        self.finalizer()


def serve(model, path, device, charge='0', multiplicity='1'):
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # Library progress is logged; only our protocol owns stdout.
    try:
        calculator = load(model, path, device, (int(charge), int(multiplicity)))
        pickle.dump({'ready': True}, writer, protocol=4)
        writer.flush()
        while True:
            try:
                request = pickle.load(reader)
            except EOFError:
                return
            atoms = Atoms(symbols=request['symbols'], positions=request['positions'], cell=request['cell'], pbc=request['pbc'])
            atoms.calc = calculator
            results = dict(energy=float(atoms.get_potential_energy()), forces=atoms.get_forces(apply_constraint=False))
            if 'stress' in calculator.results:
                results['stress'] = calculator.results['stress']
            elif 'stress' in request['properties']:
                results['stress'] = atoms.get_stress()
            if not all(np.isfinite(value).all() for value in results.values()):
                raise ValueError('模型返回非有限数值。')
            pickle.dump({'results': results}, writer, protocol=4)
            writer.flush()
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, EOFError, pickle.UnpicklingError) as error:
        pickle.dump({'error': f'{type(error).__name__}: {error}'}, writer, protocol=4)
        writer.flush()


def main():
    action, *args = sys.argv[1:]
    if action == 'serve':
        serve(*args)
    elif action == 'hardware':
        print(json.dumps({'gpu': hardware(args[0])}, ensure_ascii=False))
    elif action == 'check':
        model, path, device = args
        calculator = load(model, Path(path), device)
        from ase.build import molecule
        atoms = molecule('H2O') if MODELS[model].engine == 'deepmd' else Atoms('Fe2', positions=[[0, 0, 0], [1.43, 1.43, 1.43]], cell=np.eye(3) * 5.72, pbc=True)
        atoms.calc = calculator
        energy, forces = atoms.get_potential_energy(), atoms.get_forces()
        if not np.isfinite(energy) or forces.shape != (len(atoms), 3) or not np.isfinite(forces).all():
            raise ValueError('安装后模型实测未返回有效的能量与力。')
        print(json.dumps({'model': model, 'device': device, 'energy': energy, 'max_force': float(np.linalg.norm(forces, axis=1).max())}))


if __name__ == '__main__':
    main()
