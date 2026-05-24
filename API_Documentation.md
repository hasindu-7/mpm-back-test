# API Documentation

## Tenant Authentication

### Register Tenant
**Endpoint:** `POST /api/tenants/register/`
**Description:** Registers a new tenant, provisions a separate database, runs migrations, and creates an Owner user.
**Expects:**
```json
{
    "name": "string",
    "subdomain": "string",
    "admin_email": "string",
    "admin_password": "string"
}
```
**Returns:** 
```json
{
    "id": "uuid",
    "name": "string",
    "subdomain": "string"
}
```

### Login
**Endpoint:** `POST /api/auth/login/`
**Description:** Authenticates a user within a specific tenant context.
**Headers (Required for localhost):**
- `X-Tenant-Subdomain: <subdomain>`
**Expects:**
```json
{
    "email": "string",
    "password": "string",
    "subdomain": "string"
}
```
**Returns:**
```json
{
    "access": "jwt_token",
    "refresh": "jwt_token"
}
```

### Refresh Token
**Endpoint:** `POST /api/auth/token/refresh/`
**Description:** Get a new access token using a valid refresh token.
**Headers (Required):**
- `X-Tenant-Subdomain: <subdomain>`
**Expects:**
```json
{
    "refresh": "string"
}
```
**Returns:**
```json
{
    "access": "jwt_token"
}
```

## Project Management (Agency Manager Only)

### Create/List Projects
**Endpoint:** `GET/POST /api/projects/`
**Description:** Create a new project or list all projects. When a project is created, default roles (Project Manager, Expert, etc.) are automatically generated for it.

### Manage Project Roles
**Endpoint:** `GET/POST /api/project-roles/`
**Description:** List or create custom roles within a project.
**POST Expects:**
```json
{
    "project": "uuid-of-project",
    "name": "Social Media Lead",
    "description": "Manages organic and paid social"
}
```
**URL Params (for GET):**
- `?project_id=<uuid>`: Filter roles by project.

### Close Project
**Endpoint:** `POST /api/projects/{id}/close/`
**Description:** Mark a project as closed.

### Manage Project Members
**Endpoint:** `GET/POST /api/members/`
**Description:** Assign or list members of a project.
**POST Expects:**
```json
{
    "project": "uuid-of-project",
    "user": "uuid-of-user",
    "project_role_id": "uuid-of-project-role"
}
```
**URL Params (for GET):**
- `?project_id=<uuid>`: Filter members by project.

## Task Management (Agency Manager & Team)

### Create/List Tasks
**Endpoint:** `GET/POST /api/tasks/`
**Description:** Manage project tasks.
**POST Expects:**
```json
{
    "project": "uuid-of-project",
    "title": "Design Homepage",
    "estimated_hours": 12.5,
    "deadline": "2024-01-15T12:00:00Z",
    "priority": "heavy | medium | light"
}
```

### Task Assignments (Workload Check)
**Endpoint:** `GET/POST /api/assignments/`
**Description:** Assign team members to tasks.
**GET Returns:** Includes `workload_warning` field which alerts if the task exceeds the user's available working hours before the deadline.
**POST Expects:**
```json
{
    "task": "uuid-of-task",
    "user": "uuid-of-user"
}
```

### Task Statuses
**Endpoint:** `GET/POST /api/task-statuses/`
**Description:** Manage custom project workflows. Default statuses (To Do, In Progress, Review, Done) are auto-created.

### Task Updates & Deliverables
**Endpoint:** `GET/POST /api/updates/`
**Description:** Post comments or deliver work. 
**POST Expects:**
```json
{
    "task": "uuid-of-task",
    "user": "uuid-of-current-user",
    "content": "Final logo files attached.",
    "is_deliverable": true
}
```

## User Management

### List/Create Agency Users
**Endpoint:** `GET/POST /api/users/`
**POST Expects:**
```json
{
    "email": "teammate@agency.com",
    "password": "securepassword123",
    "first_name": "John",
    "last_name": "Doe",
    "daily_working_hours": 8.0,
    "role_id": "uuid-of-agency-role"
}
```




