class RuntimeFailure(Exception):
    stage = "runtime"
    operation_started = False


class PreconditionFailure(RuntimeFailure):
    stage = "preconditions"


class ExecutionFailure(RuntimeFailure):
    stage = "execution"
    operation_started = True


class DomainFailure(RuntimeFailure):
    stage = "domain"
