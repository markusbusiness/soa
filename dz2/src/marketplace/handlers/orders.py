from uuid import UUID

from marketplace.errors import validation_error
from marketplace.services import orders
from marketplace_api.apis.orders_api_base import BaseOrdersApi
from marketplace_api.models.extra_models import TokenModel
from marketplace_api.models.order_create import OrderCreate
from marketplace_api.models.order_item_request import OrderItemRequest
from marketplace_api.models.order_item_response import OrderItemResponse
from marketplace_api.models.order_response import OrderResponse
from marketplace_api.models.order_status_change import OrderStatusChange
from marketplace_api.models.order_update import OrderUpdate


def to_items(items: list[OrderItemRequest]) -> list[orders.Item]:
    seen: set[UUID] = set()
    for index, item in enumerate(items):
        if item.product_id in seen:
            raise validation_error(f"items[{index}].product_id", "Duplicate product_id in order items")
        seen.add(item.product_id)
    return [orders.Item(item.product_id, item.quantity) for item in items]


def to_response(view: orders.OrderView) -> OrderResponse:
    order = view.order
    return OrderResponse(
        id=order["id"],
        user_id=order["user_id"],
        status=order["status"],
        items=[
            OrderItemResponse(id=item["id"], product_id=item["product_id"], quantity=item["quantity"],
                              price_at_order=float(item["price_at_order"]))
            for item in view.items
        ],
        promo_code_id=order["promo_code_id"],
        promo_code=order["promo_code"],
        total_amount=float(order["total_amount"]),
        discount_amount=float(order["discount_amount"]),
        created_at=order["created_at"],
        updated_at=order["updated_at"],
    )


class OrdersHandler(BaseOrdersApi):
    async def create_order(self, token_bearerAuth: TokenModel, order_create: OrderCreate) -> OrderResponse:
        items = to_items(order_create.items)
        return to_response(await orders.create(token_bearerAuth, items, order_create.promo_code))

    async def get_order(self, token_bearerAuth: TokenModel, id: UUID) -> OrderResponse:
        return to_response(await orders.get(token_bearerAuth, id))

    async def update_order(self, token_bearerAuth: TokenModel, id: UUID, order_update: OrderUpdate) -> OrderResponse:
        items = to_items(order_update.items)
        return to_response(await orders.update(token_bearerAuth, id, items))

    async def cancel_order(self, token_bearerAuth: TokenModel, id: UUID) -> OrderResponse:
        return to_response(await orders.cancel(token_bearerAuth, id))

    async def change_order_status(self, token_bearerAuth: TokenModel, id: UUID,
                                  order_status_change: OrderStatusChange) -> OrderResponse:
        return to_response(await orders.change_status(id, order_status_change.status.value))
