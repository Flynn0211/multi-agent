import json
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from .bridge import OpenCodeBridgeClient


class Task(BaseModel):
    title: str
    role: Literal['planner', 'researcher', 'coder', 'reviewer']
    status: Literal['todo', 'in_progress', 'reviewing', 'done'] = 'todo'


class Decision(BaseModel):
    next: Literal['planner', 'researcher', 'coder', 'reviewer', 'FINISH']
    tasks: list[Task] = Field(min_length=1, max_length=30)
    summary: str


class Review(BaseModel):
    passed: bool
    summary: str
    evidence: list[str] = Field(min_length=1)


class AgentState(TypedDict, total=False):
    run_id: str
    prompt: str
    directory: str
    mode: str
    next: str
    tasks: list[dict]
    outputs: dict[str, str]
    turns: int
    revisions: int
    review_passed: bool
    approved: bool
    rejected: bool
    summary: str


def parse_json(text, schema):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    return schema.model_validate(json.loads(text))


def build_graph(bridge: OpenCodeBridgeClient, saver):
    async def call(state, role, instruction):
        context = {k: state.get(k) for k in ('prompt', 'directory', 'tasks', 'outputs', 'revisions', 'review_passed')}
        prompt = (f'You are the {role} in a LangGraph-controlled engineering team. '
                  'Work only on the user request in the specified directory. Do not launch subagents or ask questions; '
                  'report blockers in your final answer. Use actual tools and never invent execution results.\n'
                  + instruction + '\nContext:\n' + json.dumps(context, ensure_ascii=False))
        return await bridge.execute(state, role, prompt, f"{state['run_id']}:{state.get('turns', 0)}:{role}")

    async def supervisor(state):
        if state.get('turns', 0) >= 20:
            raise RuntimeError('Supervisor routing limit reached')
        result = parse_json(await call(state, 'supervisor',
            'Analyze and break down the request into real subtasks. Route to one specialist. '
            'Planner and researcher must run before coder, and reviewer after coder. '
            'FINISH only after reviewer passed. Return ONLY JSON matching: ' + json.dumps(Decision.model_json_schema())), Decision)
        outputs = state.get('outputs', {})
        route = result.next
        if 'planner' not in outputs:
            route = 'planner'
        elif 'researcher' not in outputs:
            route = 'researcher'
        elif 'coder' not in outputs:
            route = 'coder'
        elif 'reviewer' not in outputs:
            route = 'reviewer'
        elif not state.get('review_passed'):
            if state.get('revisions', 0) >= bridge.settings.max_revisions:
                raise RuntimeError('QA failed after maximum revisions; see reviewer evidence')
            route = 'coder'
        elif state.get('review_passed'):
            route = 'FINISH'
        tasks = [task.model_dump() for task in result.tasks]
        for task in tasks:
            if route == 'FINISH':
                task['status'] = 'done'
            elif task['role'] == route:
                task['status'] = 'reviewing' if route == 'reviewer' else 'in_progress'
        return {'next': route, 'tasks': tasks, 'summary': result.summary, 'turns': state.get('turns', 0) + 1}

    instructions = {
        'planner': 'Read actual repository files. Produce a concise architecture and actionable implementation plan. Read-only.',
        'researcher': 'Read and search the repository and relevant documentation. Return exact file references, APIs and constraints. Read-only.',
        'coder': 'Implement the requested work using tools. Follow plan and research; fix any reviewer findings. Summarize changed files.',
        'reviewer': 'Inspect changes and run relevant real tests/build/lint. Do not claim passed without successful command evidence. '
                    'Return ONLY JSON matching: ' + json.dumps(Review.model_json_schema()),
    }

    def specialist(role):
        async def node(state):
            text = await call(state, role, instructions[role])
            updates = {'outputs': {**state.get('outputs', {}), role: text}}
            if role == 'coder':
                updates.update(revisions=state.get('revisions', 0) + 1, review_passed=False)
                updates['outputs'].pop('reviewer', None)
            if role == 'reviewer':
                review = parse_json(text, Review)
                updates['review_passed'] = review.passed
            return updates
        return node

    def approval(state):
        if state['mode'] == 'human':
            answer = interrupt({'type': 'plan_approval', 'plan': state.get('outputs', {}).get('planner', ''),
                                'tasks': state.get('tasks', []), 'revision': state.get('revisions', 0) + 1})
            return {'approved': answer is True, 'rejected': answer is not True}
        return {'approved': True}

    graph = StateGraph(AgentState)
    graph.add_node('supervisor', supervisor)
    graph.add_node('approval', approval)
    for role in instructions:
        graph.add_node(role, specialist(role))
        graph.add_edge(role, 'supervisor')
    graph.add_edge(START, 'supervisor')
    graph.add_conditional_edges('supervisor', lambda s: s['next'],
                                {'planner': 'planner', 'researcher': 'researcher', 'coder': 'approval',
                                 'reviewer': 'reviewer', 'FINISH': END})
    graph.add_conditional_edges('approval', lambda s: 'coder' if s['approved'] else END)
    return graph.compile(checkpointer=saver)
