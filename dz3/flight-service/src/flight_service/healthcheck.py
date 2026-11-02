import os
import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def main() -> int:
    with grpc.insecure_channel(f"127.0.0.1:{os.getenv('GRPC_PORT', '50051')}") as channel:
        response = health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(),
            metadata=[("x-api-key", os.environ["FLIGHT_SERVICE_API_KEY"])],
            timeout=2,
        )
    return 0 if response.status == health_pb2.HealthCheckResponse.SERVING else 1


if __name__ == "__main__":
    sys.exit(main())
