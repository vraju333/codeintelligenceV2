from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class ScenarioRequest(BaseModel):
    scenario_code: str
    scenario_name: str
    http_method: str
    endpoint: str
    description: Optional[str] = None
    request_json: Optional[str] = None
    expected_response_json: Optional[str] = None
    expected_db_effect: Optional[str] = None
    involved_classes: Optional[str] = None
    status: str = "ACTIVE"


class ScenarioResponse(ScenarioRequest):
    id: int
    model_config = ConfigDict(from_attributes=True)


class JavaMethod(BaseModel):
    name: str
    return_type: Optional[str] = None
    parameters: List[str] = []


class JavaClassInfo(BaseModel):
    file_path: str
    package: Optional[str] = None
    class_name: str
    class_type: str
    extends: Optional[str] = None
    implements: List[str] = []
    annotations: List[str] = []
    imports: List[str] = []
    methods: List[JavaMethod] = []


class ScanResponse(BaseModel):
    project_path: str
    total_java_files: int
    classes: List[JavaClassInfo]




class CodeChunk(BaseModel):
    chunk_id: str
    file_path: str
    package_name: str | None = None
    class_name: str
    method_name: str | None = None
    chunk_type: str
    content: str