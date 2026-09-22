"""Zone provenance and boundary-interface metadata for convex-decomposed geometry.

The decomposition itself only returns convex parts. Downstream building-energy
workflows also need to know which parts used to be one physical space and which
of their walls exist only because of the convex split. This package carries that
information alongside the geometry.
"""
