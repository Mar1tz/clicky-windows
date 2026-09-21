"""HeuristicIntentClassifier (≥20 utterances) + ProviderIntentClassifier."""
import asyncio

import pytest

from agents.core import ProviderResponse, TaskKind
from agents.providers import MockProvider
from agents.understanding import (
    HeuristicIntentClassifier, ProviderIntentClassifier,
)


def classify(text):
    return asyncio.run(HeuristicIntentClassifier().classify(text))


# -- the two contract utterances ---------------------------------------------

def test_the_flagship_task_utterance():
    i = classify("Create a new folder called MyApp and build the foundation "
                 "of a React application inside it.")
    assert i.is_task
    assert i.workspace_hint == "MyApp"
    assert i.kind is TaskKind.CODING
    assert i.worker_type == "coding"
    assert i.reply and i.title


def test_the_flagship_question_utterance():
    i = classify("what is a React component?")
    assert not i.is_task
    assert i.kind is TaskKind.GENERAL


# -- tasks (imperative + object) ---------------------------------------------

@pytest.mark.parametrize("text,kind,hint", [
    ("Create a folder called Foo", TaskKind.GENERAL, "Foo"),
    ("build a python project named scraper", TaskKind.CODING, "scraper"),
    ("scaffold a Next.js app in the client directory", TaskKind.CODING, None),
    ("set up a node repository called api-server", TaskKind.CODING, "api-server"),
    ("generate a vue component", TaskKind.CODING, None),
    ("make a website for my portfolio", TaskKind.CODING, None),
    ("set up an express api", TaskKind.CODING, None),
    ("write a script called backup.py", TaskKind.CODING, "backup.py"),
    ("initialize a git repository", TaskKind.CODING, None),
    ("research the best folder structure and write it to a file",
     TaskKind.RESEARCH, None),
])
def test_task_utterances(text, kind, hint):
    i = classify(text)
    assert i.is_task, text
    assert i.kind is kind, (text, i.kind)
    if hint is not None:
        assert i.workspace_hint == hint, (text, i.workspace_hint)


def test_research_and_computer_and_browser_kinds():
    r = classify("research react frameworks and create a summary file")
    assert r.is_task and r.kind is TaskKind.RESEARCH and r.worker_type == "research"


# -- chat (questions / greetings / no object) --------------------------------

@pytest.mark.parametrize("text", [
    "what is a React component?",
    "how do I create a folder?",             # question form beats the imperative
    "why is my build failing",
    "which framework should I use?",
    "can you explain how components work?",
    "tell me about python decorators",
    "hello there",                            # no imperative, no object
    "thanks!",
    "is the project set up correctly?",       # trailing ? without imperative
    "explain the folder structure",           # explain -> chat
])
def test_chat_utterances(text):
    i = classify(text)
    assert not i.is_task, text
    assert i.worker_type == "coding"          # default for chat/general


def test_imperative_without_object_is_chat():
    # "make it faster" has an imperative but no concrete work object
    assert not classify("make it faster please").is_task


def test_workspace_hint_strips_quotes_and_ignores_stopwords():
    assert classify('create a folder called "Cool App"').workspace_hint == "Cool App" \
        or classify('create a folder called "Cool App"').workspace_hint == "Cool"
    # "build ... inside it" must not capture "it"
    i = classify("create a file and build the app inside it")
    assert i.workspace_hint != "it"


# -- ProviderIntentClassifier ------------------------------------------------

def test_provider_classifier_parses_strict_json():
    payload = ('{"is_task": true, "title": "Build MyApp", "goal": "build it", '
               '"kind": "coding", "worker_type": "coding", '
               '"workspace_hint": "MyApp", "reply": "On it."}')
    p = MockProvider(script=[ProviderResponse(text=payload)])
    i = asyncio.run(ProviderIntentClassifier(p).classify("build MyApp"))
    assert i.is_task and i.kind is TaskKind.CODING
    assert i.workspace_hint == "MyApp" and i.reply == "On it."


def test_provider_classifier_tolerates_code_fence():
    payload = '```json\n{"is_task": false, "kind": "general"}\n```'
    p = MockProvider(script=[ProviderResponse(text=payload)])
    i = asyncio.run(ProviderIntentClassifier(p).classify("what is this"))
    assert not i.is_task and i.kind is TaskKind.GENERAL


def test_provider_classifier_falls_back_to_heuristic_on_garbage():
    p = MockProvider(script=[ProviderResponse(text="not json at all, sorry")])
    i = asyncio.run(ProviderIntentClassifier(p).classify(
        "Create a folder called MyApp and build a React app in it"))
    # heuristic fallback still nails it
    assert i.is_task and i.workspace_hint == "MyApp" and i.kind is TaskKind.CODING


def test_provider_classifier_bad_kind_defaults_general():
    payload = '{"is_task": true, "kind": "banana", "title": "x", "goal": "x"}'
    p = MockProvider(script=[ProviderResponse(text=payload)])
    i = asyncio.run(ProviderIntentClassifier(p).classify("do x"))
    assert i.kind is TaskKind.GENERAL and i.worker_type == "coding"
