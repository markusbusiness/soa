import grpc


class ApiKeyInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        metadata = grpc.aio.Metadata(*(client_call_details.metadata or ()))
        metadata["x-api-key"] = self._api_key
        return await continuation(client_call_details._replace(metadata=metadata), request)
