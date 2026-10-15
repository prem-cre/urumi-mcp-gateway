import json

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import SecretStr

from services.gateway.chat import (
    _complete_from_provider,
    _usage_and_cost,
    choose_model_route,
    execute_tool_loop,
)
from services.gateway.config import Settings


def make_settings(**overrides):
    values = {
        "database_url": "postgresql+asyncpg://gateway:unused@localhost/gateway",
        "api_key_pepper": SecretStr("api-pepper"),
        "session_pepper": SecretStr("session-pepper"),
        "upstream_encryption_key": SecretStr(Fernet.generate_key().decode()),
        "bootstrap_admin_email": "admin@example.test",
        "bootstrap_admin_password": SecretStr("not-a-real-password"),
        "bootstrap_demo_token": SecretStr("demo-token"),
        "woo_mcp_token": SecretStr("woo-token"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_chat_loop_calls_only_the_advertised_gateway_tool_then_returns_final_answer():
    settings = make_settings()
    tool = Tool(
        name="woocommerce__list_products",
        description="List products from WooCommerce.",
        inputSchema={"type": "object", "properties": {"search": {"type": "string"}}},
    )
    model_requests = []
    gateway_calls = []
    observations = []

    async def complete(messages, model_tools):
        model_requests.append((messages, model_tools))
        if len(model_requests) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "woocommerce__list_products",
                                        "arguments": json.dumps({"search": "notebook"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "One notebook is available."}}]
        }

    async def gateway_call(name, arguments):
        gateway_calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text='[{"name":"Demo Notebook"}]')])

    async def observe(observation):
        observations.append(observation)

    result = await execute_tool_loop(
        "Find the notebook",
        [tool],
        gateway_call,
        complete,
        settings,
        history=[{"role": "user", "content": "We are looking at products."}],
        selected_model="standard-model",
        provider="test-provider",
        route_tier="standard",
        route_reason="single-source query",
        observation_writer=observe,
    )

    assert result.response == "One notebook is available."
    assert gateway_calls == [("woocommerce__list_products", {"search": "notebook"})]
    assert result.tools_called[0].server == "woocommerce"
    assert result.tools_called[0].outcome == "success"
    assert model_requests[0][1][0]["function"]["name"] == "woocommerce__list_products"
    assert model_requests[1][0][1] == {
        "role": "user",
        "content": "We are looking at products.",
    }
    assert model_requests[1][0][4]["role"] == "tool"
    assert [item.kind for item in observations] == ["generation", "tool", "generation"]
    assert observations[0].model == "standard-model"
    assert observations[1].name == "woocommerce__list_products"
    assert observations[1].status == "success"


@pytest.mark.asyncio
async def test_chat_loop_bounds_tool_calls_and_does_not_execute_malformed_arguments():
    settings = make_settings()
    tool = Tool(name="inventory__check_stock", inputSchema={"type": "object"})
    called = False
    completions = 0

    async def complete(_messages, _tools):
        nonlocal completions
        completions += 1
        if completions > 1:
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "Tool request was malformed."}}
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "bad_call",
                                "function": {"name": "inventory__check_stock", "arguments": "["},
                            }
                        ]
                    }
                }
            ]
        }

    async def gateway_call(_name, _arguments):
        nonlocal called
        called = True
        return CallToolResult(content=[TextContent(type="text", text="unexpected")])

    result = await execute_tool_loop("Check stock", [tool], gateway_call, complete, settings)

    assert called is False
    assert result.tools_called[0].outcome == "denied"
    assert result.response == "Tool request was malformed."


@pytest.mark.asyncio
async def test_chat_stops_after_a_tool_reports_an_unknown_write_outcome():
    settings = make_settings()
    tool = Tool(name="woocommerce__create_product", inputSchema={"type": "object"})
    completion_count = 0
    gateway_call_count = 0

    async def complete(_messages, _tools):
        nonlocal completion_count
        completion_count += 1
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "create_1",
                                "function": {
                                    "name": "woocommerce__create_product",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    }
                }
            ]
        }

    async def gateway_call(_name, _arguments):
        nonlocal gateway_call_count
        gateway_call_count += 1
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text="The upstream timed out after dispatch. A write may have completed; the gateway did not retry it.",
                )
            ],
            isError=True,
        )

    result = await execute_tool_loop("Create a product", [tool], gateway_call, complete, settings)

    assert completion_count == 1
    assert gateway_call_count == 1
    assert result.tools_called[0].outcome == "unknown_outcome"
    assert "stopped before making another tool call" in result.response


@pytest.mark.asyncio
async def test_provider_request_uses_openai_compatible_url_model_and_server_side_key():
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Ready."}}]},
        )

    config = make_settings(
        llm_base_url="https://model.example.test/v1/",
        llm_model="assessment-model",
        llm_api_key=SecretStr("test-only-provider-key"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _complete_from_provider(
            client,
            config,
            [{"role": "user", "content": "Hello"}],
            [],
        )

    assert captured["url"] == "https://model.example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-only-provider-key"
    assert captured["body"]["model"] == "assessment-model"
    assert result["choices"][0]["message"]["content"] == "Ready."


def test_model_route_uses_explainable_standard_and_complex_profiles():
    settings = make_settings(
        llm_model="fallback",
        llm_standard_model="fast-alias",
        llm_complex_model="strong-alias",
    )
    tools = [
        Tool(name="woocommerce__list_orders", inputSchema={"type": "object"}),
        Tool(name="inventory__check_stock", inputSchema={"type": "object"}),
    ]

    standard = choose_model_route("What orders arrived today?", tools, settings)
    complex_route = choose_model_route(
        "Find today's orders and check inventory for each product.", tools, settings
    )

    assert standard[0] == "standard"
    assert standard[2] == "fast-alias"
    assert complex_route[0] == "complex"
    assert complex_route[2] == "strong-alias"
    assert "multi-step" in complex_route[1] or "cross-server" in complex_route[1]

    delegated = choose_model_route(
        "What orders arrived today?",
        tools,
        make_settings(llm_model="", llm_router_model="litellm-auto-router"),
    )
    assert delegated == (
        "auto",
        "Delegated complexity classification and model selection to the configured router model.",
        "litellm-auto-router",
    )


def test_usage_cost_estimate_uses_provider_usage_and_configured_per_million_rates():
    settings = make_settings(
        llm_model_pricing={"provider/fast": {"input_per_million": 0.15, "output_per_million": 0.60}}
    )

    usage = _usage_and_cost(
        {
            "model": "provider/fast",
            "usage": {"prompt_tokens": 2000, "completion_tokens": 500, "total_tokens": 2500},
        },
        "fast-alias",
        settings,
    )

    assert usage == (2000, 500, 2500, 0.0003, 0.0003)
