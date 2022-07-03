import pytest
import spacy

from spacy import util
from spacy.training import Example
from spacy.lang.en import English
from spacy.tests.util import make_tempdir
from spacy.ml.models.coref_util import (
    DEFAULT_CLUSTER_PREFIX,
    select_non_crossing_spans,
    get_sentence_ids,
    spans2ints,
)

from thinc.util import has_torch

# fmt: off
TRAIN_DATA = [
    (
        "John Smith picked up the red ball and he threw it away.",
        {
            "spans": {
                f"{DEFAULT_CLUSTER_PREFIX}_1": [
                    (0, 10, "MENTION"),      # John Smith
                    (38, 40, "MENTION"),     # he

                ],
                f"{DEFAULT_CLUSTER_PREFIX}_2": [
                    (25, 33, "MENTION"),     # red ball
                    (47, 49, "MENTION"),     # it
                ],
                f"coref_head_clusters_1": [
                    (5, 10, "MENTION"),      # Smith
                    (38, 40, "MENTION"),     # he

                ],
                f"coref_head_clusters_2": [
                    (29, 33, "MENTION"),     # red ball
                    (47, 49, "MENTION"),     # it
                ]
            }
        },
    ),
]
# fmt: on

CONFIG = {"model": {"@architectures": "spacy.SpanPredictor.v1", "tok2vec_size": 64}}


@pytest.fixture
def nlp():
    return English()


@pytest.fixture
def snlp():
    en = English()
    en.add_pipe("sentencizer")
    return en


@pytest.mark.skipif(not has_torch, reason="Torch not available")
def test_add_pipe(nlp):
    nlp.add_pipe("span_predictor")
    assert nlp.pipe_names == ["span_predictor"]


@pytest.mark.skipif(not has_torch, reason="Torch not available")
def test_not_initialized(nlp):
    nlp.add_pipe("span_predictor")
    text = "She gave me her pen."
    with pytest.raises(ValueError, match="E109"):
        nlp(text)


@pytest.mark.skipif(not has_torch, reason="Torch not available")
def test_span_predictor_serialization(nlp):
    # Test that the span predictor component can be serialized
    nlp.add_pipe("span_predictor", last=True, config=CONFIG)
    nlp.initialize()
    assert nlp.pipe_names == ["span_predictor"]
    text = "She gave me her pen."
    doc = nlp(text)

    with make_tempdir() as tmp_dir:
        nlp.to_disk(tmp_dir)
        nlp2 = spacy.load(tmp_dir)
        assert nlp2.pipe_names == ["span_predictor"]
        doc2 = nlp2(text)

        assert spans2ints(doc) == spans2ints(doc2)


@pytest.mark.skipif(not has_torch, reason="Torch not available")
def test_overfitting_IO(nlp):
    # Simple test to try and quickly overfit - ensuring the ML models work correctly
    train_examples = []
    for text, annot in TRAIN_DATA:
        train_examples.append(Example.from_dict(nlp.make_doc(text), annot))

    nlp.add_pipe("span_predictor", config=CONFIG)
    optimizer = nlp.initialize()
    test_text = TRAIN_DATA[0][0]
    doc = nlp(test_text)

    # Needs ~12 epochs to converge
    for i in range(15):
        losses = {}
        nlp.update(train_examples, sgd=optimizer, losses=losses)
        doc = nlp(test_text)

    # test the trained model
    doc = nlp(test_text)

    # Also test the results are still the same after IO
    with make_tempdir() as tmp_dir:
        nlp.to_disk(tmp_dir)
        nlp2 = util.load_model_from_path(tmp_dir)
        doc2 = nlp2(test_text)

    # Make sure that running pipe twice, or comparing to call, always amounts to the same predictions
    texts = [
        test_text,
        "I noticed many friends around me",
        "They received it. They received the SMS.",
    ]
    docs1 = list(nlp.pipe(texts))
    docs2 = list(nlp.pipe(texts))
    docs3 = [nlp(text) for text in texts]
    assert spans2ints(docs1[0]) == spans2ints(docs2[0])
    assert spans2ints(docs1[0]) == spans2ints(docs3[0])


@pytest.mark.skipif(not has_torch, reason="Torch not available")
def test_tokenization_mismatch(nlp):
    train_examples = []
    for text, annot in TRAIN_DATA:
        eg = Example.from_dict(nlp.make_doc(text), annot)
        ref = eg.reference
        char_spans = {}
        for key, cluster in ref.spans.items():
            char_spans[key] = []
            for span in cluster:
                char_spans[key].append((span[0].idx, span[-1].idx + len(span[-1])))
        with ref.retokenize() as retokenizer:
            # merge "picked up"
            retokenizer.merge(ref[2:4])

        # Note this works because it's the same doc and we know the keys
        for key, _ in ref.spans.items():
            spans = char_spans[key]
            ref.spans[key] = [ref.char_span(*span) for span in spans]

        # Finally, copy over the head spans to the pred
        pred = eg.predicted
        for key, val in ref.spans.items():
            if key.startswith("coref_head_clusters"):
                spans = char_spans[key]
                pred.spans[key] = [pred.char_span(*span) for span in spans]

        train_examples.append(eg)

    nlp.add_pipe("span_predictor", config=CONFIG)
    optimizer = nlp.initialize()
    test_text = TRAIN_DATA[0][0]
    doc = nlp(test_text)

    for i in range(100):
        losses = {}
        nlp.update(train_examples, sgd=optimizer, losses=losses)
        doc = nlp(test_text)

    # test the trained model; need to use doc with head spans on it already
    test_doc = train_examples[0].predicted
    doc = nlp(test_doc)

    # XXX DEBUG
    print("SPANS", len(doc.spans))
    for key, val in doc.spans.items():
        print(key, val)
        print("...")

    # Also test the results are still the same after IO
    with make_tempdir() as tmp_dir:
        nlp.to_disk(tmp_dir)
        nlp2 = util.load_model_from_path(tmp_dir)
        doc2 = nlp2(test_text)

    # Make sure that running pipe twice, or comparing to call, always amounts to the same predictions
    texts = [
        test_text,
        "I noticed many friends around me",
        "They received it. They received the SMS.",
    ]

    # save the docs so they don't get garbage collected
    docs1 = list(nlp.pipe(texts))
    docs2 = list(nlp.pipe(texts))
    docs3 = [nlp(text) for text in texts]
    assert spans2ints(docs1[0]) == spans2ints(docs2[0])
    assert spans2ints(docs1[0]) == spans2ints(docs3[0])
    assert False

