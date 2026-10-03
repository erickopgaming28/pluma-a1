"""Conversión en un solo trabajador, con progreso y descarte de tareas obsoletas."""
from concurrent.futures import ThreadPoolExecutor, CancelledError
import threading
import uuid


class ConversionQueue:
    def __init__(self, limit=8, retained=24):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pluma-imagen')
        self._lock = threading.RLock()
        self._tasks = {}
        self.limit, self.retained = limit, retained

    def start(self, convert):
        with self._lock:
            if sum(t['state'] in ('queued', 'running') for t in self._tasks.values()) >= self.limit:
                raise ValueError('Hay varias imágenes en preparación. Espera a que terminen antes de añadir otra.')
            task_id = uuid.uuid4().hex
            task = {'state': 'queued', 'percent': 0, 'message': 'Esperando turno para preparar la imagen…',
                    'cancel': threading.Event()}
            self._tasks[task_id] = task
            task['future'] = self._executor.submit(self._run, task, convert)
            self._trim()
            return task_id

    def _trim(self):
        for key in list(self._tasks):
            if len(self._tasks) <= self.retained:
                break
            if self._tasks[key]['state'] not in ('queued', 'running'):
                del self._tasks[key]

    def _run(self, task, convert):
        def update(percent, message):
            if task['cancel'].is_set():
                raise CancelledError()
            with self._lock:
                task.update(state='running', percent=percent, message=message)
        try:
            update(1, 'Preparando la imagen…')
            result = convert(update, task['cancel'].is_set)
            with self._lock:
                if task['cancel'].is_set():
                    raise CancelledError()
                task.update(state='done', percent=100, message='Imagen lista.', result=result)
        except CancelledError:
            with self._lock:
                task.update(state='canceled', message='Se descartó la conversión anterior.')
        except Exception as error:
            with self._lock:
                task.update(state='failed', message=str(error))

    def snapshot(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError('La conversión caducó. Cambia un ajuste para volver a prepararla.')
            return {k: v for k, v in task.items() if k not in ('cancel', 'future')}

    def cancel(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task['cancel'].set()
                task['future'].cancel()
                task.update(state='canceled', message='Se descartó la conversión anterior.')
        return {'state': 'canceled'}
