"""Compose the retained v4 extraction core; v5 resolves its targets before writing."""
from pathlib import Path
from .workflow import Workflow1Extractor


def create_workflow1_extractor(client,*,trace_root:Path,admission=None)->Workflow1Extractor:
    return Workflow1Extractor(client,trace_root,admission=admission)
