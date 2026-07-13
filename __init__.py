"""
BPCL Kota–Bijwasan Thermal UFP Quantification System
=====================================================

A pure-physics engine for quantifying Unaccounted-For Product (UFP)
on the BPCL/MMBL Kota → Bharatpur → Piyala → Bijwasan petroleum pipeline.

All thermal and volumetric computations follow published standards:
  - API MPMS Chapter 11.1 (VCF / CTL)
  - Johansen (1975) soil thermal conductivity
  - Darcy-Weisbach / Colebrook-White friction
  - ERA5-Land reanalysis for soil climate data

No machine learning. No hardcoded temperatures. Pure physics.
"""

__version__ = "1.0.0"
