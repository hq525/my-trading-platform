from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

# All money crosses the API as strings — no float rounding in transit.
Money = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]


class LoginIn(BaseModel):
    password: str
