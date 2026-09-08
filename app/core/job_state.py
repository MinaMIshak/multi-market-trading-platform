from enum import StrEnum


class SchedulerJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    MISSED = "MISSED"
    SKIPPED = "SKIPPED"
