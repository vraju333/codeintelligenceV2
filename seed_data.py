import json

from database import SessionLocal
from db_models import Scenario
from repositories.scenario_repository import ScenarioRepository
from schemas import ScenarioRequest


repository = ScenarioRepository()


SCENARIOS = [
    ScenarioRequest(
        scenario_code="EMPLOYEE_ADD",
        scenario_name="Full Employee Creation",
        http_method="POST",
        endpoint="/api/persons",
        description="Creates a full employee through the generic PersonController using type EMPLOYEE.",
        request_json=json.dumps({"type": "EMPLOYEE"}),
        expected_db_effect="Employee data and shared child details are persisted.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,PersonRequestValidator,EmployeeService,EmployeeServiceImpl,EmployeeMapper,AbstractPersonMapper,EmailDetailsMapper,ContactDetailsMapper,AddressMapper,EmployeeRepository,Employee"
    ),
    ScenarioRequest(
        scenario_code="EMPLOYEE_UPDATE",
        scenario_name="Employee Full Update",
        http_method="PUT",
        endpoint="/api/persons/{id}",
        description="Updates employee attributes and shared child details.",
        request_json=json.dumps({"type": "EMPLOYEE"}),
        expected_db_effect="Employee and supplied shared child values are updated.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,EmployeeService,EmployeeServiceImpl,EmployeeMapper,AbstractPersonMapper,EmailDetailsMapper,ContactDetailsMapper,AddressMapper,EmployeeRepository,Employee"
    ),
    ScenarioRequest(
        scenario_code="EMPLOYEE_DELETE",
        scenario_name="Employee Deletion",
        http_method="DELETE",
        endpoint="/api/persons/{id}",
        description="Deletes an employee using type EMPLOYEE.",
        request_json=json.dumps({"type": "EMPLOYEE"}),
        expected_db_effect="Employee record is deleted.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,EmployeeService,EmployeeServiceImpl,EmployeeRepository,Employee"
    ),
    ScenarioRequest(
        scenario_code="EMPLOYEE_EMAIL_UPDATE",
        scenario_name="Employee Email Update",
        http_method="PATCH",
        endpoint="/api/persons/{id}/email",
        description="Attribute-focused employee email update.",
        request_json=json.dumps({"type": "EMPLOYEE", "primaryEmail": "updated@example.com"}),
        expected_db_effect="Employee email details are updated.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,EmployeeService,EmployeeServiceImpl,EmployeeMapper,EmailDetailsMapper,EmployeeRepository,Employee,EmailDetails"
    ),
    ScenarioRequest(
        scenario_code="EMPLOYEE_CONTACT_UPDATE",
        scenario_name="Employee Contact Update",
        http_method="PATCH",
        endpoint="/api/persons/{id}/contact",
        description="Shared ContactDetailsMapper employee update scenario.",
        request_json=json.dumps({"type": "EMPLOYEE", "primaryContact": "9555555555"}),
        expected_db_effect="Employee contact details are updated.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,EmployeeService,EmployeeServiceImpl,EmployeeMapper,ContactDetailsMapper,EmployeeRepository,Employee,ContactDetails"
    ),
    ScenarioRequest(
        scenario_code="STUDENT_ADD",
        scenario_name="Full Student Creation",
        http_method="POST",
        endpoint="/api/persons",
        description="Creates a full student through the generic PersonController using type STUDENT.",
        request_json=json.dumps({"type": "STUDENT"}),
        expected_db_effect="Student data and shared child details are persisted.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,PersonRequestValidator,StudentService,StudentServiceImpl,StudentMapper,AbstractPersonMapper,EmailDetailsMapper,ContactDetailsMapper,AddressMapper,StudentRepository,Student"
    ),
    ScenarioRequest(
        scenario_code="STUDENT_UPDATE",
        scenario_name="Student Full Update",
        http_method="PUT",
        endpoint="/api/persons/{id}",
        description="Updates student attributes and shared child details.",
        request_json=json.dumps({"type": "STUDENT"}),
        expected_db_effect="Student and supplied shared child values are updated.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,StudentService,StudentServiceImpl,StudentMapper,AbstractPersonMapper,EmailDetailsMapper,ContactDetailsMapper,AddressMapper,StudentRepository,Student"
    ),
    ScenarioRequest(
        scenario_code="STUDENT_DELETE",
        scenario_name="Student Deletion",
        http_method="DELETE",
        endpoint="/api/persons/{id}",
        description="Deletes a student using type STUDENT.",
        request_json=json.dumps({"type": "STUDENT"}),
        expected_db_effect="Student record is deleted.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,StudentService,StudentServiceImpl,StudentRepository,Student"
    ),
    ScenarioRequest(
        scenario_code="STUDENT_ADDRESS_UPDATE",
        scenario_name="Student Address Update",
        http_method="PATCH",
        endpoint="/api/persons/{id}/address",
        description="Shared address child update for a student.",
        request_json=json.dumps({"type": "STUDENT", "city": "Hyderabad"}),
        expected_db_effect="Student address is updated.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,StudentService,StudentServiceImpl,StudentMapper,AddressMapper,StudentRepository,Student,Address"
    ),
    ScenarioRequest(
        scenario_code="STUDENT_CONTACT_UPDATE",
        scenario_name="Student Contact Update",
        http_method="PATCH",
        endpoint="/api/persons/{id}/contact",
        description="Shared ContactDetailsMapper student update scenario.",
        request_json=json.dumps({"type": "STUDENT", "primaryContact": "7555555555"}),
        expected_db_effect="Student contact details are updated.",
        involved_classes="PersonController,PersonService,PersonServiceImpl,StudentService,StudentServiceImpl,StudentMapper,ContactDetailsMapper,StudentRepository,Student,ContactDetails"
    ),
]

# Known codes from the original demo seed. Reuse their rows where possible so
# existing IDs/baseline history are not thrown away.
LEGACY_MIGRATIONS = {
    "ADD_EMPLOYEE": "EMPLOYEE_ADD",
    "UPDATE_PHONE_CONTACT": "EMPLOYEE_CONTACT_UPDATE",
    "UPDATE_EMAIL_DETAILS": "EMPLOYEE_EMAIL_UPDATE",
}


def _apply_request(existing: Scenario, request: ScenarioRequest):
    payload = request.model_dump()
    for key, value in payload.items():
        setattr(existing, key, value)


def seed_scenarios():
    db = SessionLocal()
    try:
        # Migrate legacy seed rows only when the new scenario code does not
        # already exist. This preserves scenario IDs and prior history.
        for old_code, new_code in LEGACY_MIGRATIONS.items():
            old = repository.find_by_code(db, old_code)
            new = repository.find_by_code(db, new_code)
            if old and not new:
                old.scenario_code = new_code
                db.flush()

        for request in SCENARIOS:
            existing = repository.find_by_code(db, request.scenario_code)
            if existing:
                _apply_request(existing, request)
            else:
                scenario = Scenario(**request.model_dump())
                db.add(scenario)

        db.commit()
    finally:
        db.close()
