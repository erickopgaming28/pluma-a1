"""Exercise launcher control flow with fake Python commands, without installing or serving.

The temporary BAT swaps interpreter executables for callable CMD shims and removes
its pauses. All directory creation remains inside the verified temporary directory.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
HEALTHY = r'''@echo off
if "%~1"=="-c" (
  if "%~2"=="import sys" exit /b 0
  echo CHECK "%~f0" >> "%PLUMA_TEST_LOG%"
  if "%PLUMA_TEST_DEP_READY%"=="1" exit /b 0
  exit /b 1
)
if "%~1"=="-m" (
  if "%~2"=="venv" (
    echo CREATE >> "%PLUMA_TEST_LOG%"
    if not exist "%~3\Scripts" mkdir "%~3\Scripts"
    copy /y "%PLUMA_TEST_TEMPLATE%" "%~3\Scripts\python.cmd" >nul
    exit /b 0
  )
  if "%~2"=="pip" (
    echo INSTALL >> "%PLUMA_TEST_LOG%"
    if "%PLUMA_TEST_PIP_FAIL%"=="1" exit /b 1
    set "PLUMA_TEST_DEP_READY=1"
    exit /b 0
  )
)
if "%~1"=="-u" (
  echo START "%~f0" >> "%PLUMA_TEST_LOG%"
  exit /b %PLUMA_TEST_APP_EXIT%
)
exit /b 99
'''
BROKEN = '@echo off\nexit /b 1\n'


@unittest.skipUnless(os.name == 'nt', 'Windows BAT launcher')
class LauncherTests(unittest.TestCase):
    def launch_mock(self, *, old=None, runtime=None, py=True, python=True,
                    dependencies=True, pip_fail=False, app_exit=0):
        with tempfile.TemporaryDirectory(prefix='Pluma launcher ') as temporary:
            directory = Path(temporary).resolve()
            self.assertTrue(directory.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            # Each fake command returns an exit status just like a real Python executable.
            template = directory / 'healthy.cmd'
            template.write_text(HEALTHY, encoding='ascii')
            for name, healthy in [('.venv', old), ('.venv-runtime', runtime)]:
                if healthy is None:
                    continue
                executable = directory / name / 'Scripts' / 'python.cmd'
                executable.parent.mkdir(parents=True)
                executable.write_text(HEALTHY if healthy else BROKEN, encoding='ascii')
            for name, healthy in [('py.cmd', py), ('python.cmd', python)]:
                (directory / name).write_text(HEALTHY if healthy else BROKEN, encoding='ascii')
            source = (PROJECT / 'Iniciar Pluma A1.bat').read_text(encoding='ascii')
            source = source.replace('python.exe', 'python.cmd')
            source = source.replace('"%~1" -c', 'call "%~1" -c')
            source = source.replace('"%pluma_python%"', 'call "%pluma_python%"')
            source = source.replace('\npy -', '\ncall "%PLUMA_TEST_PY%" -')
            source = source.replace('\npython -', '\ncall "%PLUMA_TEST_PYTHON%" -')
            source = source.replace('pause >nul', 'rem test: no pause')
            launcher = directory / 'Iniciar Pluma A1.bat'
            launcher.write_text(source, encoding='ascii')
            log = directory / 'calls.txt'
            env = os.environ.copy()
            env.update(PLUMA_TEST_LOG=str(log), PLUMA_TEST_TEMPLATE=str(template),
                       PLUMA_TEST_PY=str(directory / 'py.cmd'), PLUMA_TEST_PYTHON=str(directory / 'python.cmd'),
                       PLUMA_TEST_DEP_READY='1' if dependencies else '0',
                       PLUMA_TEST_PIP_FAIL='1' if pip_fail else '0', PLUMA_TEST_APP_EXIT=str(app_exit))
            result = subprocess.run(['cmd.exe', '/d', '/c', str(launcher)], cwd=directory,
                                    env=env, capture_output=True, text=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            calls = log.read_text(encoding='ascii') if log.exists() else ''
            if old is False:
                # Selecting the recovery runtime must not modify the broken old environment.
                self.assertEqual((directory / '.venv/Scripts/python.cmd').read_text(encoding='ascii'), BROKEN)
            return result, calls

    def test_healthy_existing_environment_is_preferred(self):
        result, calls = self.launch_mock(old=True, runtime=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('START "', calls)
        self.assertIn('\\.venv\\Scripts\\python.cmd', calls)
        self.assertNotIn('\\.venv-runtime\\Scripts\\python.cmd', calls)
        self.assertNotIn('CREATE', calls)
        self.assertNotIn('INSTALL', calls)

    def test_broken_environment_uses_existing_recovery_environment(self):
        result, calls = self.launch_mock(old=False, runtime=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('\\.venv-runtime\\Scripts\\python.cmd', calls)
        self.assertNotIn('CREATE', calls)

    def test_missing_launcher_falls_back_to_system_python(self):
        result, calls = self.launch_mock(old=False, py=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.count('CREATE'), 1)
        self.assertIn('\\.venv-runtime\\Scripts\\python.cmd', calls)

    def test_missing_dependency_installs_and_rechecks(self):
        result, calls = self.launch_mock(old=True, dependencies=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls.count('INSTALL'), 1)
        self.assertEqual(calls.count('CHECK'), 2)
        self.assertIn('START "', calls)

    def test_failed_install_does_not_start_server(self):
        result, calls = self.launch_mock(old=True, dependencies=False, pip_fail=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('START "', calls)
        self.assertIn('No se pudo preparar', result.stdout)

    def test_python_unavailable_explains_recovery(self):
        result, calls = self.launch_mock(old=False, py=False, python=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('START "', calls)
        self.assertIn('No se encontro un Python', result.stdout)
        self.assertIn('Add python.cmd to PATH', result.stdout)  # Extension substituted only in the test BAT.

    def test_application_exit_status_is_preserved(self):
        result, calls = self.launch_mock(runtime=True, app_exit=9)
        self.assertEqual(result.returncode, 9)
        self.assertIn('START "', calls)
        self.assertIn('El inicio termino con un error', result.stdout)


if __name__ == '__main__':
    unittest.main()
