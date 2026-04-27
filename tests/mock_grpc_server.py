#!/usr/bin/env python3
"""
Mock gRPC server for testing without C++ server.
"""
import grpc
from concurrent import futures
import sys
import os

# Add proto path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api_server', 'proto'))

import compute_pb2
import compute_pb2_grpc


class MockComputeService(compute_pb2_grpc.ComputeServiceServicer):
    def Process(self, request, context):
        """Mock non-streaming response"""
        print(f"[Mock] Received request: model={request.model_id}, input={request.input[:50]}...")
        response = compute_pb2.ProcessResponse(
            output=f"Mock response for: {request.input}",
            success=True,
            error_message=""
        )
        return response

    def ProcessStream(self, request, context):
        """Mock streaming response"""
        print(f"[Mock Stream] Received request: model={request.model_id}")
        
        # Simulate streaming output token by token
        response_text = f"This is a mock streaming response for your query about: {request.input[:30]}"
        words = response_text.split()
        
        for i, word in enumerate(words):
            chunk = compute_pb2.StreamChunk(
                content=word + " ",
                is_final=False,
                error_message=""
            )
            yield chunk
        
        # Final chunk
        yield compute_pb2.StreamChunk(content="", is_final=True, error_message="")


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    compute_pb2_grpc.add_ComputeServiceServicer_to_server(MockComputeService(), server)
    server.add_insecure_port('[::]:9000')
    server.start()
    print("Mock gRPC server started on port 9000")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
