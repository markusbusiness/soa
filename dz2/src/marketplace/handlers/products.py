from typing import Optional
from uuid import UUID

import asyncpg

from marketplace.services import products
from marketplace.services.pricing import money
from marketplace_api.apis.products_api_base import BaseProductsApi
from marketplace_api.models.extra_models import TokenModel
from marketplace_api.models.product_create import ProductCreate
from marketplace_api.models.product_page import ProductPage
from marketplace_api.models.product_response import ProductResponse
from marketplace_api.models.product_status import ProductStatus
from marketplace_api.models.product_update import ProductUpdate


def to_response(row: asyncpg.Record) -> ProductResponse:
    return ProductResponse(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        price=float(row["price"]),
        stock=row["stock"],
        category=row["category"],
        status=row["status"],
        seller_id=row["seller_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def to_fields(body: ProductCreate | ProductUpdate) -> products.ProductFields:
    return products.ProductFields(
        name=body.name,
        description=body.description,
        price=money(body.price),
        stock=body.stock,
        category=body.category,
        status=body.status.value,
    )


class ProductsHandler(BaseProductsApi):
    async def create_product(self, token_bearerAuth: TokenModel, product_create: ProductCreate) -> ProductResponse:
        return to_response(await products.create(token_bearerAuth, to_fields(product_create)))

    async def get_product(self, token_bearerAuth: TokenModel, id: UUID) -> ProductResponse:
        return to_response(await products.get(id))

    async def list_products(self, token_bearerAuth: TokenModel, page: Optional[int], size: Optional[int],
                            status: Optional[ProductStatus], category: Optional[str]) -> ProductPage:
        rows, total = await products.list_page(page, size, status.value if status else None, category)
        return ProductPage(items=[to_response(row) for row in rows], total_elements=total, page=page, size=size)

    async def update_product(self, token_bearerAuth: TokenModel, id: UUID, product_update: ProductUpdate) -> ProductResponse:
        return to_response(await products.update(token_bearerAuth, id, to_fields(product_update)))

    async def delete_product(self, token_bearerAuth: TokenModel, id: UUID) -> ProductResponse:
        return to_response(await products.archive(token_bearerAuth, id))
