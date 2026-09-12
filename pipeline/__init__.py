"""OULAD analytics pipeline.

This package must not import Django. It reads the seven OULAD CSV files,
builds weekly per-student features, trains and evaluates classifiers, and is
runnable and testable without a server.
"""
