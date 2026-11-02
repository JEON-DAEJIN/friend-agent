"""테스트는 실제 AI를 부르지 않는다. 가짜 응답(fake)과 서버 메모리 저장소로 실행한다.

실제 AI 호출은 비용이 들고, 느리고, 같은 질문에도 답이 달라서 통과와 실패를 판정할 수 없다.
"""
import os

os.environ.update({"LLM_PROVIDER": "fake", "LLM_API_KEY": "", "STORAGE": "memory", "AUTH": "none"})
os.environ.setdefault("ENV", "test")
# .env 에 Langfuse 키가 있어도 테스트가 실제 관측 도구로 트레이스를 보내지 않게 끈다.
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
