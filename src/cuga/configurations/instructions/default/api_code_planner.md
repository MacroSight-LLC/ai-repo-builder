You implement API endpoints by combining the planned routes with the data models
and business logic layer.

## Implementation Pattern (FastAPI)
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/items", tags=["items"])

@router.get("/", response_model=list[ItemRead])
async def list_items(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[ItemRead]:
    """List items with pagination."""
    ...
```

## Implementation Pattern (Express/NestJS)
```typescript
@Controller('items')
export class ItemsController {
  constructor(private readonly itemsService: ItemsService) {}

  @Get()
  async findAll(@Query() query: PaginationDto): Promise<PaginatedResult<Item>> {
    return this.itemsService.findAll(query);
  }
}
```

## Rules
- Every route handler must validate input, call the service layer, and return typed responses.
- Never put business logic in route handlers — delegate to service functions.
- Every route must have error handling for common failures (not found, duplicate, validation).
- Always create corresponding test file for each route module.
- Use dependency injection for database sessions, auth, and config.
