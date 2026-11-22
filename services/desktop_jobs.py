"""Cancellable desktop operations that publish results only while active."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from uuid import uuid4

from services.local_process import TaskCancelled, cancellable, check_cancelled


class DesktopJob:
    def __init__(self, document_id=None):
        self.event = Event()
        self.lock = RLock()
        self.committing = False
        self.state = {'id': uuid4().hex, 'status': 'queued'}
        if document_id:
            self.state['documentId'] = document_id

    def snapshot(self):
        with self.lock:
            return self.state.copy()

    def progress(self, message, **data):
        with self.lock:
            check_cancelled()
            self.state['progress'] = {'message': message, **data}

    def publish(self, operation):
        with self.lock:
            check_cancelled()
            self.committing = True
        result = operation()
        with self.lock:
            self.state.update(status='succeeded', result=result)

    def cancel(self):
        with self.lock:
            if self.state['status'] in ('queued', 'running') and not self.committing:
                self.event.set()
                self.state['status'] = 'cancelled'
            return self.state.copy()


class DesktopJobs:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='utopia-desktop')
        self._jobs = {}

    def submit(self, operation, document_id=None):
        job = DesktopJob(document_id)
        while len(self._jobs) >= 128:
            expired = next((key for key, old in self._jobs.items()
                            if old.snapshot()['status'] in ('succeeded', 'failed', 'cancelled')), None)
            if expired is None:
                raise RuntimeError('任务队列已满，请稍后重试。')
            self._jobs.pop(expired)
        queued = job.snapshot()
        self._jobs[queued['id']] = job
        self._executor.submit(self._execute, job, operation)
        result = {'taskId': queued['id'], 'status': 'queued'}
        if document_id:
            result['documentId'] = document_id
        return result

    @staticmethod
    def _execute(job, operation):
        try:
            with cancellable(job.event):
                with job.lock:
                    check_cancelled()
                    job.state['status'] = 'running'
                operation(job)
        except TaskCancelled:
            job.cancel()
        except Exception as error:
            with job.lock:
                if job.state['status'] != 'cancelled':
                    job.state.update(status='failed', error=str(error) or type(error).__name__)
                    if getattr(error, 'log', None):
                        job.state['log'] = error.log

    def _job(self, params):
        ident = params.get('id', params.get('taskId'))
        if not isinstance(ident, str) or ident not in self._jobs:
            raise ValueError('找不到该后台任务，任务可能在上次启动前运行。')
        return self._jobs[ident]

    def get(self, params):
        return self._job(params).snapshot()

    def cancel(self, params):
        return self._job(params).cancel()

    def close(self):
        for job in self._jobs.values():
            job.cancel()
        self._executor.shutdown(cancel_futures=True)
