"""适配层验证响应失败与字面 token 契约；这些测试不代替真实模型验收。"""

from typing import Any

import pytest

from novel_lens.embedding import EmbeddingClient
from novel_lens.embedding_runtime import Endpoint
from novel_lens.errors import ServiceError


class ResponseEndpoint(Endpoint):
    def __init__(self, response: Any) -> None:
        super().__init__(18081)
        self.response = response
        self.body: dict[str, Any] | None = None

    def request(self, route: str, body: dict[str, Any] | None = None, timeout: int = 120) -> Any:
        self.body = body
        return self.response


def test_literal_tokenizer_input_preserves_source() -> None:
    endpoint = ResponseEndpoint({"tokens": [1, 2, 3]})
    model = EmbeddingClient(None)
    model.endpoint = endpoint
    value = " 空白\n<|endoftext|> "
    assert model.tokenize(value) == [1, 2, 3]
    assert endpoint.body == {"content": value, "add_special": True, "parse_special": False}


@pytest.mark.parametrize(
    "change", ["dimension", "nan", "zero", "norm", "count", "order", "usage", "shape"]
)
def test_bad_response_never_becomes_vector(change: str) -> None:
    good = [1.0] + [0.0] * 1023
    response: Any = {"data": [{"index": 0, "embedding": good}], "usage": {"prompt_tokens": 3}}
    if change == "dimension":
        response["data"][0]["embedding"] = good[:-1]
    elif change in ("nan", "zero", "norm"):
        response["data"][0]["embedding"][0] = {"nan": float("nan"), "zero": 0, "norm": 2}[change]
    elif change == "count":
        response["data"] = []
    elif change == "order":
        response["data"][0]["index"] = 1
    elif change == "usage":
        response["usage"]["prompt_tokens"] = 2
    else:
        response = None
    model = EmbeddingClient(None)
    model.endpoint = ResponseEndpoint(response)
    with pytest.raises(ServiceError) as error:
        model.embed([[1, 2, 3]])
    assert error.value.code == "EMBEDDING_INVALID_RESPONSE"
