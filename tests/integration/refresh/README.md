# Charm Refresh Integration Tests

This directory contains comprehensive integration tests for the charm-refresh functionality in the charmed-etcd-operator.

## Test Structure

### Test Files

- **`test_refresh_basic.py`** - Basic refresh functionality tests
  - Configuration options testing
  - Pre-refresh checks
  - Snap revision tracking
  - Data integrity verification
  - Basic cluster health monitoring

- **`test_refresh_advanced.py`** - Advanced refresh scenarios
  - Rolling refresh with continuous workload
  - Different pause strategies
  - Refresh interruption and resume
  - Leadership changes during refresh
  - Concurrent operations
  - Cluster size changes
  - Failure scenarios and recovery
  - Performance and scalability testing

- **`test_refresh_actions.py`** - Refresh action testing
  - `pre-refresh-check` action with various parameters
  - `force-refresh-start` action with different configurations
  - `resume-refresh` action testing
  - Action parameter validation
  - Action sequencing and integration

### Helper Files

- **`refresh_helpers.py`** - Common helper functions and utilities
  - `RefreshTestContext` - Context manager for refresh testing
  - Cluster health monitoring functions
  - Snap revision tracking utilities
  - Data integrity verification
  - Action execution helpers
  - Workload simulation functions

- **`conftest.py`** - Pytest configuration and fixtures
  - Common test fixtures
  - Logging configuration
  - Test markers and collections

## Running Tests

### Prerequisites

1. Ensure the charmed-etcd-operator has refresh functionality implemented
2. Have a Juju controller and model available
3. Install test dependencies:
   ```bash
   pip install pytest pytest-asyncio pytest-operator
   ```

### Running All Refresh Tests

```bash
# Run all refresh tests
pytest tests/integration/refresh/

# Run with verbose output
pytest -v tests/integration/refresh/

# Run only basic tests
pytest tests/integration/refresh/test_refresh_basic.py

# Run only action tests
pytest tests/integration/refresh/test_refresh_actions.py

# Run advanced tests (these may take longer)
pytest tests/integration/refresh/test_refresh_advanced.py
```

### Running Specific Test Categories

```bash
# Run only fast tests (exclude slow tests)
pytest -m "not slow" tests/integration/refresh/

# Run only refresh-specific tests
pytest -m "refresh" tests/integration/refresh/

# Run with specific log level
pytest --log-cli-level=DEBUG tests/integration/refresh/
```

### Test Environment

The tests require:
- A deployed charmed-etcd-operator with refresh functionality
- Network access for snap operations
- Sufficient permissions for snap refresh operations
- Juju model with appropriate resources

## Test Coverage

### Functionality Tested

1. **Configuration Management**
   - `pause_after_unit_refresh` options (none, first, all)
   - Configuration validation and persistence

2. **Refresh Actions**
   - `pre-refresh-check` - Cluster readiness validation
   - `force-refresh-start` - Initiate refresh process
   - `resume-refresh` - Resume interrupted refresh

3. **Cluster Operations**
   - Data integrity during refresh
   - Cluster health monitoring
   - Leadership changes
   - Concurrent operations
   - Scaling scenarios

4. **Error Handling**
   - Network partition simulation
   - Resource constraints
   - Rollback scenarios
   - Action parameter validation

5. **Performance**
   - Refresh timing metrics
   - Scalability testing
   - Workload simulation during refresh

### Test Scenarios

1. **Basic Scenarios**
   - Single unit refresh
   - Multi-unit cluster refresh
   - Configuration changes
   - Health verification

2. **Advanced Scenarios**
   - Rolling refresh with active workload
   - Refresh interruption and recovery
   - Concurrent refresh operations
   - Resource-constrained environments

3. **Edge Cases**
   - Network failures during refresh
   - Leadership changes during refresh
   - Cluster size changes
   - Action execution on non-leader units

## Expected Outcomes

### Successful Test Run

When tests pass, it indicates:
- Refresh functionality is properly implemented
- Cluster remains healthy during refresh operations
- Data integrity is maintained
- Actions execute correctly with appropriate parameters
- Error handling works as expected

### Test Failures

Common failure scenarios:
- Refresh actions not implemented
- Snap packages not available for refresh
- Cluster becomes unhealthy during operations
- Data integrity violations
- Timeout issues in test environment

## Debugging

### Enable Debug Logging

```bash
pytest --log-cli-level=DEBUG tests/integration/refresh/
```

### Run Single Test with Detailed Output

```bash
pytest -v -s tests/integration/refresh/test_refresh_basic.py::TestRefreshBasicFunctionality::test_pre_refresh_check_action
```

### Common Issues

1. **Timeout Errors** - Increase timeouts in test configuration
2. **Action Not Found** - Ensure refresh actions are properly defined in actions.yaml
3. **Snap Refresh Failures** - Check snap availability and permissions
4. **Cluster Health Issues** - Verify initial cluster deployment is healthy

## Contributing

When adding new refresh tests:

1. Follow the existing test structure and naming conventions
2. Use the `RefreshTestContext` context manager for cleanup
3. Add appropriate test markers (slow, unit, integration)
4. Include both positive and negative test cases
5. Document any new helper functions
6. Ensure tests are idempotent and don't interfere with each other 