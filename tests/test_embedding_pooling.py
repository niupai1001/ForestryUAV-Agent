"""Padding and invalid outputs cannot silently change retrieval evidence."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest


def test_masked_pooling_excludes_padding_and_normalizes():
    path = Path(__file__).resolve().parents[1]/'research/encode_discovery_once.py'
    spec = importlib.util.spec_from_file_location('embedding_pooling',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hidden = np.asarray([[[3.,0.],[0.,4.],[999.,999.]]])
    assert np.allclose(module.pool_hidden(hidden,np.asarray([[1,1,0]])),[[.6,.8]])
    with pytest.raises(ValueError,match='Empty'):
        module.pool_hidden(hidden,np.asarray([[0,0,0]]))
    with pytest.raises(ValueError,match='zero'):
        module.pool_hidden(np.zeros((1,1,2)),np.asarray([[1]]))
