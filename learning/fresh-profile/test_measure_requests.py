import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).with_name("measure_requests.py")
SPEC = importlib.util.spec_from_file_location("measure_requests", MODULE)
measure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure)


class RequestMeasurementTests(unittest.TestCase):
    def test_requires_every_container_request(self):
        with tempfile.TemporaryDirectory(dir=measure.HERE) as directory:
            path = Path(directory) / "missing.yaml"
            path.write_text("""apiVersion: apps/v1
kind: Deployment
metadata: {name: x, namespace: x}
spec:
  replicas: 1
  template:
    spec:
      containers: [{name: x, image: example.invalid/x}]
""", encoding="utf-8")
            with self.assertRaises(measure.MeasurementError):
                measure.measure([path])

    def test_replica_multiplication(self):
        with tempfile.TemporaryDirectory(dir=measure.HERE) as directory:
            path = Path(directory) / "requests.yaml"
            path.write_text("""apiVersion: apps/v1
kind: Deployment
metadata: {name: x, namespace: x}
spec:
  replicas: 2
  template:
    spec:
      containers:
      - {name: x, image: example.invalid/x, resources: {requests: {cpu: 25m, memory: 32Mi}}}
""", encoding="utf-8")
            result = measure.measure([path])
            self.assertEqual((result["cpuMilli"], result["memoryMiB"]), (50, 64))


if __name__ == "__main__":
    unittest.main(verbosity=2)
