import hmac
import logging

import grpc

logger = logging.getLogger("flight_service.auth")

API_KEY_HEADER = "x-api-key"


class ApiKeyInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, api_key: str):
        self._api_key = api_key.encode()

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return None
        metadata = dict(handler_call_details.invocation_metadata or ())
        presented = metadata.get(API_KEY_HEADER)
        if presented is not None and hmac.compare_digest(presented.encode(), self._api_key):
            return handler
        reason = "missing" if presented is None else "invalid"
        logger.warning("rejected %s: %s api key", handler_call_details.method, reason)
        return _deny(handler, reason)


def _deny(handler: grpc.RpcMethodHandler, reason: str) -> grpc.RpcMethodHandler:
    message = f"{reason} {API_KEY_HEADER} metadata"

    async def abort(_request, context):
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, message)

    async def abort_stream(_request, context):
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, message)
        yield

    serializers = {
        "request_deserializer": handler.request_deserializer,
        "response_serializer": handler.response_serializer,
    }
    if handler.unary_unary:
        return grpc.unary_unary_rpc_method_handler(abort, **serializers)
    if handler.unary_stream:
        return grpc.unary_stream_rpc_method_handler(abort_stream, **serializers)
    if handler.stream_unary:
        return grpc.stream_unary_rpc_method_handler(abort, **serializers)
    return grpc.stream_stream_rpc_method_handler(abort_stream, **serializers)
