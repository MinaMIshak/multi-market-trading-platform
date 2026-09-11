"""Offline, in-memory M6 paper execution; no operational integration."""
from .models import PaperExecutionConfig, PaperSimulationInput, PaperSimulationResult
from .simulator import simulate_paper

__all__ = ['PaperExecutionConfig', 'PaperSimulationInput', 'PaperSimulationResult',
           'simulate_paper']
