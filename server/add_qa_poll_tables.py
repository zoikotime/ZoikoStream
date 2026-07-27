from sqlalchemy import text
from app.db import engine


def add_qa_poll_tables():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS qa_questions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                stream_id UUID NOT NULL REFERENCES streams(id),
                user_id UUID REFERENCES users(id),
                display_name VARCHAR(120) NOT NULL,
                text VARCHAR(500) NOT NULL,
                votes INTEGER NOT NULL DEFAULT 0,
                answered BOOLEAN NOT NULL DEFAULT FALSE,
                is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_qa_questions_stream_id ON qa_questions (stream_id);"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS qa_votes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                question_id UUID NOT NULL REFERENCES qa_questions(id),
                voter_key VARCHAR(160) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_qa_vote_voter UNIQUE (question_id, voter_key)
            );
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_qa_votes_question_id ON qa_votes (question_id);"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS polls (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                stream_id UUID NOT NULL REFERENCES streams(id),
                question VARCHAR(300) NOT NULL,
                is_closed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_polls_stream_id ON polls (stream_id);"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS poll_options (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                poll_id UUID NOT NULL REFERENCES polls(id),
                label VARCHAR(160) NOT NULL,
                position INTEGER NOT NULL,
                votes INTEGER NOT NULL DEFAULT 0
            );
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_poll_options_poll_id ON poll_options (poll_id);"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS poll_votes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                poll_id UUID NOT NULL REFERENCES polls(id),
                option_id UUID NOT NULL REFERENCES poll_options(id),
                voter_key VARCHAR(160) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_poll_vote_voter UNIQUE (poll_id, voter_key)
            );
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_poll_votes_poll_id ON poll_votes (poll_id);"))

        conn.commit()

    print("qa_questions/qa_votes/polls/poll_options/poll_votes tables created successfully")


if __name__ == "__main__":
    add_qa_poll_tables()
