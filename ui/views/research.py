"""
Research Agent tab — LangGraph ReAct agent for enforcement research.

Provides a text area for describing GDPR situations, streams agent
tool calls and reasoning, and displays a structured Research Brief.
"""

from __future__ import annotations

import uuid

import psycopg
import streamlit as st


def render(conn: psycopg.Connection) -> None:
    st.markdown("### Enforcement Research Agent")
    st.markdown(
        "Describe your situation and the agent will research "
        "precedents, analyze factors, and estimate your exposure."
    )

    # Session state
    if "research_thread_id" not in st.session_state:
        st.session_state["research_thread_id"] = f"session-{uuid.uuid4().hex[:8]}"
    if "research_history" not in st.session_state:
        st.session_state["research_history"] = []

    query = st.text_area(
        "Describe your GDPR situation",
        placeholder=(
            "We are a fintech in Spain that had a data breach affecting 50,000 users. "
            "We notified the AEPD within 48 hours and fully cooperated. "
            "What fine range can we expect?"
        ),
        height=120,
        key="research_query",
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        run = st.button("Research", type="primary", disabled=not query)
    with col2:
        if st.button("New Session"):
            st.session_state["research_thread_id"] = f"session-{uuid.uuid4().hex[:8]}"
            st.session_state["research_history"] = []
            st.rerun()

    if run and query:
        _run_research(conn, query)

    # Show conversation history
    for entry in st.session_state["research_history"]:
        if entry["role"] == "user":
            st.markdown(f"**You:** {entry['content']}")
        else:
            st.markdown(entry["content"])
        st.divider()


def _run_research(conn: psycopg.Connection, query: str) -> None:
    """Execute the agent and stream results."""
    try:
        from services.agent import create_agent, stream_agent
    except ImportError as e:
        st.error(
            f"Agent dependencies not installed: {e}\n\n"
            "Run: `pip install langgraph langchain-anthropic langchain-core`"
        )
        return

    # Save user query
    st.session_state["research_history"].append({"role": "user", "content": query})

    # Create agent (cached per connection)
    if "research_agent" not in st.session_state:
        with st.spinner("Initializing agent..."):
            st.session_state["research_agent"] = create_agent(conn)

    agent = st.session_state["research_agent"]
    thread_id = st.session_state["research_thread_id"]

    with st.status("Researching...", expanded=True) as status:
        final_content = ""
        tool_calls_shown = 0

        try:
            for event in stream_agent(agent, query, thread_id=thread_id):
                if event["type"] == "tool_call":
                    tool_calls_shown += 1
                    st.write(f"Calling **{event['name']}**...")
                elif event["type"] == "tool_result":
                    st.write(f"{event['name']} completed")
                elif event["type"] == "response":
                    final_content = event["content"]

            status.update(
                label=f"Research complete ({tool_calls_shown} tools used)",
                state="complete",
            )
        except Exception as e:
            status.update(label="Research failed", state="error")
            st.error(f"Agent error: {e}")
            return

    if final_content:
        st.session_state["research_history"].append({
            "role": "assistant",
            "content": final_content,
        })
        st.markdown(final_content)
