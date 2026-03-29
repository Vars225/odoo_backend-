from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from config import settings
import logging

logger = logging.getLogger(__name__)

client: AsyncIOMotorClient = None
database: AsyncIOMotorDatabase = None


async def connect_db():
    global client, database
    try:
        client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
        database = client.get_default_database()
        # Ping to verify connection
        await client.admin.command("ping")
        logger.info("✅ Connected to MongoDB")
        await create_indexes()
    except Exception as e:
        logger.warning(f"⚠️ MongoDB not available: {e}")
        logger.warning("⚠️ Server starting WITHOUT database. API endpoints requiring DB will fail.")
        logger.warning("⚠️ Set MONGO_URI in .env to a valid MongoDB instance (e.g. MongoDB Atlas)")


async def disconnect_db():
    global client
    if client:
        client.close()
        logger.info("MongoDB disconnected")


async def create_indexes():
    """Create necessary indexes for performance."""
    db = get_db()

    # Users
    await db.users.create_index("email", unique=True)
    await db.users.create_index("company_id")

    # Expenses
    await db.expenses.create_index("user_id")
    await db.expenses.create_index("company_id")
    await db.expenses.create_index("status")
    await db.expenses.create_index("created_at")

    # Approvals
    await db.approvals.create_index("expense_id")
    await db.approvals.create_index("approver_id")
    await db.approvals.create_index([("expense_id", 1), ("step", 1)])

    # Payments
    await db.payments.create_index("expense_id", unique=True)
    await db.payments.create_index("stripe_payment_id")

    logger.info("✅ Database indexes created")


def get_db() -> AsyncIOMotorDatabase:
    return database