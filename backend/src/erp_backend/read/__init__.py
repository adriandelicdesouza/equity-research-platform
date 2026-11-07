"""Read-only query services behind the data API (D59).

Every function here takes a :class:`~sqlalchemy.orm.Session`, issues SELECTs only and
returns response schemas. Route handlers stay thin; business rules (provenance, conflict
semantics, versioning) come from the stored records and the existing domain modules, not
from re-implementations in the API layer.
"""
