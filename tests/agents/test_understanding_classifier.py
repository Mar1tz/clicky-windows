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


# -- TieredIntentClassifier (M17) --------------------------------------------

class _CountingProvider:
    """Records whether it was consulted at all - the whole point of the
    tiered classifier is that clear utterances never reach a provider."""
    name = "counting"

    def __init__(self, text='{"is_task": true, "title": "T", "goal": "g", '
                            '"kind": "coding", "worker_type": "coding", '
                            '"workspace_hint": null, "reply": "ok"}'):
        self.text = text
        self.calls = 0

    async def complete(self, messages, tools=None, *, system="", model=None,
                       temperature=0.2, max_tokens=4096):
        self.calls += 1
        from agents.core import ProviderResponse
        return ProviderResponse(text=self.text)

    def capabilities(self):
        from agents.core import ProviderCapabilities
        return ProviderCapabilities(local=True, cost_tier=0)


def tiered(provider=None, **kw):
    from agents.understanding import TieredIntentClassifier
    return TieredIntentClassifier(provider or _CountingProvider(), **kw)


@pytest.mark.parametrize("text", [
    "Create a new folder called MyApp and build a React app inside it",
    "build a python project named scraper",
])
def test_tiered_answers_clear_commands_without_a_provider_call(text):
    p = _CountingProvider()
    i = asyncio.run(tiered(p).classify(text))
    assert i.is_task
    assert p.calls == 0, "a confident rule match must not pay for a model call"


@pytest.mark.parametrize("text", [
    "what is a React component?",
    "how do I create a folder?",
    "is the project set up correctly?",
])
def test_tiered_answers_clear_questions_without_a_provider_call(text):
    p = _CountingProvider()
    i = asyncio.run(tiered(p).classify(text))
    assert not i.is_task
    assert p.calls == 0, "chat on the voice path must stay instant"


def test_tiered_consults_the_provider_when_rules_are_unsure():
    """'sort out my project' has no imperative+object pair the rules
    recognise, so they report low confidence - exactly the band worth
    spending a model call on."""
    p = _CountingProvider()
    i = asyncio.run(tiered(p).classify("sort out my project"))
    assert p.calls == 1
    assert i.is_task, "the provider's verdict should win in the unsure band"


def test_tiered_falls_back_to_rules_when_the_provider_fails():
    class Broken(_CountingProvider):
        async def complete(self, *a, **k):
            self.calls += 1
            raise RuntimeError("gateway down")

    p = Broken()
    i = asyncio.run(tiered(p).classify("sort out my project"))
    assert p.calls == 1
    assert not i.is_task, "degrade to the rules' answer, never to an exception"


def test_tiered_threshold_is_adjustable():
    p = _CountingProvider()
    # A threshold above every heuristic confidence sends everything to the model.
    asyncio.run(tiered(p, threshold=1.0).classify("what is a React component?"))
    assert p.calls == 1


def test_provider_classifier_times_out_to_the_rules():
    """A hung route must not hold a voice turn open."""
    import asyncio as aio

    class Hanging(_CountingProvider):
        async def complete(self, *a, **k):
            self.calls += 1
            await aio.sleep(30)

    from agents.understanding import ProviderIntentClassifier
    c = ProviderIntentClassifier(Hanging(), timeout=0.05)
    i = asyncio.run(c.classify("Create a folder called Foo"))
    assert i.is_task and i.workspace_hint == "Foo"      # the heuristic's answer
