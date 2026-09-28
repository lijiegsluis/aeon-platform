"""Database performance optimizations: indexes and query improvements."""
from sqlalchemy import Index, text
from aeon_nimbus import db as D


def create_performance_indexes():
    """Create indexes on frequently queried columns."""
    engine = D.engine

    indexes = [
        # Company search indexes
        Index('idx_company_ticker', D.Company.ticker),
        Index('idx_company_sector', D.Company.sector),
        Index('idx_company_country', D.Company.country),
        Index('idx_company_slug', D.Company.slug),

        # ProposedFact indexes for data studio
        Index('idx_proposed_company', D.ProposedFact.company_id),
        Index('idx_proposed_status', D.ProposedFact.status),

        # Job indexes
        Index('idx_job_company', D.Job.company_id),
        Index('idx_job_status', D.Job.status),
    ]

    with engine.connect() as conn:
        for idx in indexes:
            try:
                idx.create(engine, checkfirst=True)
                print(f"Created index: {idx.name}")
            except Exception as e:
                print(f"Index {idx.name} already exists or failed: {e}")
        conn.commit()


def optimize_database():
    """Run VACUUM and ANALYZE on SQLite database."""
    engine = D.engine

    with engine.connect() as conn:
        # VACUUM cannot run in transaction
        conn.execute(text("VACUUM"))
        conn.execute(text("ANALYZE"))
        conn.commit()

    print("Database optimized")


if __name__ == "__main__":
    print("Creating performance indexes...")
    create_performance_indexes()
    print("\nOptimizing database...")
    optimize_database()
    print("Done!")
