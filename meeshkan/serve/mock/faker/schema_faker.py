########################
#### FAKER
########################
import json
import random
from functools import reduce
from typing import Any, Mapping, Union, Sequence, cast

from faker import Faker
from http_types import Request, Response
from openapi_typed_2 import convert_from_openapi, Reference, OpenAPIObject

from meeshkan.serve.mock.faker.faker_base import MeeshkanFakerBase
from meeshkan.serve.mock.faker.faker_exception import FakerException
from meeshkan.serve.mock.matcher import get_response_from_ref, ref_name, change_ref, change_refs
from meeshkan.serve.mock.storage import Storage


class MeeshkanSchemaFaker(MeeshkanFakerBase):
    _LO = -99999999
    _HI = 99999999

    def __init__(self, text_faker: Faker, request: Request, spec: OpenAPIObject, storage: Storage):
        super().__init__(text_faker, request, spec, storage)

    # to prevent too-nested objects
    def sane_depth(self, n):
        return max([0, 3 - n])

    def fake_object(self, schema: Any, depth: int) -> Any:
        addls = (
            {}
            if "additionalProperties" not in schema
            else {
                k: v
                for k, v in [
                    (
                        self._fkr.name(),
                        random.random()
                        if (isinstance(schema["additionalProperties"], bool))
                           and (schema["additionalProperties"] is True)
                        else self.fake_it(schema["additionalProperties"], depth),
                    )
                    for x in range(random.randint(0, 4))
                ]
            }
        )
        properties = (
            [] if "properties" not in schema else [x for x in schema["properties"].keys()]
        )
        random.shuffle(properties)
        properties = (
            []
            if len(properties) == 0
            else properties[
                 : min([self.sane_depth(depth), random.randint(0, len(properties) - 1)])
                 ]
        )
        properties = list(
            set(([] if "required" not in schema else schema["required"]) + properties)
        )
        return {
            **addls,
            **{
                k: v
                for k, v in [
                    (p, self.fake_it(schema["properties"][p], depth))
                    for p in properties
                ]
            },
        }

    def fake_array(self, schema: Any, depth: int) -> Any:
        mn = 0 if "minItems" not in schema else schema["minItems"]
        mx = 100 if "maxItems" not in schema else schema["maxItems"]

        if "items" not in schema:
            return []
        elif isinstance(schema["items"], list):
            return [self.fake_it(x, depth) for x in schema["items"]]
        else:
            items_schema = schema["items"]
            count = range(random.randint(mn, mx))
            if "$ref" in items_schema:
                return self.fake_ref(items_schema, depth, count)
            else:
                return [self.fake_it(schema["items"], depth) for _ in range(random.randint(mn, mx))]

    def fake_any_of(self, schema: Any, depth: int) -> Any:
        return self.fake_it(random.choice(schema["anyOf"]), depth)

    def fake_all_of(self, schema: Any, depth: int) -> Any:
        return reduce(
            lambda a, b: {**a, **b},
            [self.fake_it(x, depth) for x in schema["allOf"]],
            {},
        )

    def fake_one_of(self, schema: Any, depth: int) -> Any:
        return self.fake_it(random.choice(schema["oneOf"]), depth)

    # TODO - make this work
    def fake_not(self, schema: Any, depth: int) -> Any:
        return {}

    # TODO - make this not suck
    def fake_string(self, schema: Any) -> str:
        return random.choice(schema["enum"]) if "enum" in schema else self._fkr.name()

    def fake_boolean(self, schema: Any) -> bool:
        return (
            random.choice(schema["enum"])
            if "enum" in schema
            else True
            if random.random() > 0.5
            else False
        )

    # TODO: add exclusiveMinimum and exclusiveMaximum
    def fake_integer(self, schema: Any) -> int:
        mn = self._LO if "minimum" not in schema else schema["minimum"]
        mx = self._HI if "maximum" not in schema else schema["maximum"]
        return random.choice(schema["enum"]) if "enum" in schema else random.randint(mn, mx)

    def fake_ref(self, schema: Any, depth: int, count: int = 1):
        name = schema["$ref"].split("/")[2]
        if count==1:
            return self.fake_it(self._top_schema["definitions"][name], depth)
        else:
            return [self.fake_it(self._top_schema["definitions"][name], depth) for _ in count]


    def fake_null(self, schema: Any) -> None:
        return None

    def fake_number(self, schema: Any) -> float:
        mn = self._LO if "minimum" not in schema else schema["minimum"]
        mx = self._HI if "maximum" not in schema else schema["maximum"]
        return (
            random.choice(schema["enum"])
            if "enum" in schema
            else (random.random() * (mx - mn)) + mn
        )

    def fake_it(self, schema: Any, depth: int) -> Any:
        depth += 1
        return (
            self.fake_array(schema, depth)
            if ("type" in schema) and (schema["type"] == "array")
            else self.fake_any_of(schema, depth)
            if "anyOf" in schema
            else self.fake_all_of(schema, depth)
            if "allOf" in schema
            else self.fake_one_of(schema, depth)
            if "oneOf" in schema
            else self.fake_not(schema, depth)
            if "not" in schema
            else self.fake_ref(schema, depth)
            if "$ref" in schema
            else self.fake_object(schema, depth)
            if ("type" not in schema) or (schema["type"] == "object")
            else self.fake_string(schema)
            if schema["type"] == "string"
            else self.fake_integer(schema)
            if schema["type"] == "integer"
            else self.fake_boolean(schema)
            if schema["type"] == "boolean"
            else self.fake_null(schema)
            if schema["type"] == "null"
            else self.fake_number(schema)
            if schema["type"] == "number"
            else {}
        )

    def execute(self):
        path_candidate = random.choice([x for x in self._spec.paths.values()])
        method = getattr(path_candidate, self._request.method.value, None)

        responses_error = "While a stub for a specification exists for this endpoint, it contains no responses. That usually means the schema is corrupt or it has been constrained too much (ie asking for a 201 response when it only has 200 and 400)."
        if method.responses is None or len(method.responses) == 0:
            raise FakerException(responses_error)

        status_code, response = random.choice([r for r in method.responses.items()])
        status_code = int(status_code if status_code != "default" else 400)

        response = (
            get_response_from_ref(self._spec, ref_name(response))
            if isinstance(response, Reference)
            else response
        )
        if response is None:
            raise FakerException(responses_error)
        headers: Mapping[str, Union[str, Sequence[str]]] = {}
        if response.headers is not None:
            # TODO: can't handle references yet, need to fix
            headers = (
                {}
            )  # { k: (faker(v['schema'], v['schema'], 0) if 'schema' in v else '***') for k,v in headers.items() }
        if (response.content is None) or len(response.content.items()) == 0:
            return Response(
                statusCode=status_code,
                body="",
                headers=headers,
                bodyAsJson=None,
                timestamp=None,
            )
        mime_types = response.content.keys()
        if "application/json" in mime_types:
            content = response.content["application/json"]
            if content.schema is None:
                raise FakerException(responses_error)

            schema = content.schema
            ct: Mapping[str, Union[str, Sequence[str]]] = {
                "Content-Type": "application/json"
            }
            new_headers: Mapping[str, Union[str, Sequence[str]]] = {**headers, **ct}
            if schema is None:
                return Response(
                    statusCode=status_code,
                    body="",
                    bodyAsJson="",
                    headers=new_headers,
                    timestamp=None,
                )
            to_fake = {
                **convert_from_openapi(
                    change_ref(schema)
                    if isinstance(schema, Reference)
                    else change_refs(schema)
                ),
                "definitions": {
                    k: convert_from_openapi(
                        change_ref(v) if isinstance(v, Reference) else change_refs(v)
                    )
                    for k, v in (
                        self._spec.components.schemas.items()
                        if (self._spec.components is not None)
                           and (self._spec.components.schemas is not None)
                        else []
                    )
                },
            }
            self._top_schema = to_fake
            self._generated_data = self._update_data(method)
            bodyAsJson = self.fake_it(to_fake, 0)
            return Response(
                statusCode=status_code,
                body=json.dumps(bodyAsJson),
                bodyAsJson=bodyAsJson,
                headers=new_headers,
                timestamp=None,
            )
        elif "text/plain" in mime_types:
            return Response(
                statusCode=status_code,
                body=self._fkr.sentence(),
                # TODO: can this be accomplished without a cast?
                headers=cast(
                    Mapping[str, Union[str, Sequence[str]]],
                    {**headers, "Content-Type": "text/plain"},
                ),
                bodyAsJson=None,
                timestamp=None,
            )
        else:
            raise FakerException("Could not produce content for these mime types %s" % str(mime_types))
