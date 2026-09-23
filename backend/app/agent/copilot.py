"""Russian intent router. Every factual statement is built from tool output."""

import re

from pydantic import ValidationError

from ..api.models import CopilotResponse, CopilotRequest
from ..api.store import Snapshot
from .tools import LocalTools, ToolInputError

ROLE_LABELS = {
    "consolidator": "признаки консолидации", "transit": "признаки транзита",
    "distributor": "признаки распределения", "terminal": "возможное удержание средств",
    "coordinator": "кандидат на координирующую позицию", "peripheral": "периферийная позиция",
}
BASE_LIMITATION = "Выводы — аналитические гипотезы, а не доказательство виновности; видны только внутрибанковские операции от 5 000 KZT в исходящей выборке."


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " KZT"


def respond(snapshot: Snapshot, request: CopilotRequest) -> CopilotResponse:
    message = request.message.strip().lower()
    tools = LocalTools(snapshot)
    observations: list[str] = []
    hypotheses: list[str] = []
    limitations = [BASE_LIMITATION]
    used: list[dict] = []
    links: list[dict] = []
    mentioned: set[int] = set()

    def node_link(gid: int) -> None:
        if gid not in mentioned:
            mentioned.add(gid)
            links.append({"type": "node", "id": gid, "label": f"gid {gid}"})
            node = snapshot.by_gid.get(gid)
            if node:
                for limitation in node["limitations"]:
                    if limitation not in limitations:
                        limitations.append(limitation)

    def invoke(name: str, parameters: dict, summary: str) -> dict:
        result = tools.invoke(name, parameters)
        used.append({"name": name, "parameters": parameters, "summary": summary})
        return result

    def describe_node(node: dict) -> None:
        gid = node["gid"]
        node_link(gid)
        metrics = node["metrics"]
        observations.append(f"gid {gid}: приоритет {node['priority_score']:.3f}; получено {money(metrics['in_sum'])} от {metrics['in_degree']} отправителей, отправлено {money(metrics['out_sum'])} {metrics['out_degree']} получателям.")
        hypotheses.append(f"gid {gid}: {ROLE_LABELS[node['role']]}. {node['evidence']}")

    # IDs are parameters, never instructions. Cluster numbers are separated from
    # client IDs for bridge and cluster-profile intents.
    explicit = []
    for group in re.findall(r"(?:gid|гид|клиентов|клиента|клиент|узлов|узла|узел)\s*[#:=]?\s*(\d+(?:\s*(?:,|;|и)\s*\d+)*)", message):
        explicit.extend(int(value) for value in re.findall(r"\d+", group))
    cluster_intent = "кластер" in message and not explicit
    numbers = [int(value) for value in re.findall(r"\b\d+\b", message)]
    node_intent = any(word in message for word in ("сравн", "связ", "почему", "куда", "дальше", "откуда", "получател", "отправител", "собира"))
    without_controls = re.sub(r"(?:глубин\w*|шаг\w*|топ|первых|сумм\w*)\s*\d+", "", message)
    fallback_ids = [int(value) for value in re.findall(r"\b\d+\b", without_controls)] if node_intent and not cluster_intent else []
    gids = list(dict.fromkeys(explicit or fallback_ids))[:30]
    if request.context_gid is not None and not gids and not cluster_intent:
        gids = [request.context_gid]
    gid = gids[0] if gids else None

    try:
        if any(word in message for word in ("не хватает", "недоста", "запросить", "дополнительн", "каких данных")):
            result = invoke("suggest_next_data_request", {"gid": gid}, "Сформирован перечень данных для проверки гипотезы")
            observations.extend(result["requests"])
            if gid is not None:
                node_link(gid)
                profile = invoke("get_node_profile", {"gid": gid}, f"Прочитан профиль gid {gid}")
                observations.insert(0, f"gid {gid}: наблюдается {profile['metrics']['in_degree']} отправителей и {profile['metrics']['out_degree']} получателей.")
            else:
                top = invoke("get_top_nodes", {"limit": 3}, "Выбраны узлы для начала проверки")
                for node in top["nodes"]:
                    node_link(node["gid"])
                if top["nodes"]:
                    observations.insert(0, "Запрос можно начать с узлов: " + ", ".join(f"gid {n['gid']} (приоритет {n['priority_score']:.3f})" for n in top["nodes"]) + ".")
            answer = "Для проверки нужны данные за пределами текущей выборки."
        elif "сравн" in message and len(gids) >= 2:
            result = invoke("compare_nodes", {"gids": gids[:10]}, "Сопоставлены наблюдаемые потоки и структурные показатели")
            for node in result["nodes"]:
                describe_node(node)
            answer = "Сравнение клиентов по одним и тем же наблюдаемым показателям."
        elif any(word in message for word in ("почему", "приоритет", "в топе", "объясни")) and gid is not None:
            result = invoke("explain_priority", {"gid": gid}, f"Разобраны факторы приоритета gid {gid}")
            node_link(gid)
            observations.append(f"gid {gid}: priority score {result['priority_score']:.3f}, выраженность роли {result['role_score']:.3f}.")
            quality = snapshot.by_gid[gid]["metrics"]["observation_quality"]
            for factor in sorted(result["priority_factors"], key=lambda f: -abs(f["contribution"]))[:5]:
                observations.append(f"{factor['label']}: значение {factor['value']:.3f} × вес {factor['weight']:.3f} × полнота {quality:.3f} = вклад {factor['contribution']:.3f}.")
            hypotheses.append(f"gid {gid}: {ROLE_LABELS[result['role']]}. {result['evidence']}")
            answer = f"gid {gid} попал в приоритетный список по совокупности измеренных признаков."
        elif any(word in message for word in ("соединяют", "между кластер", "мост", "соединяющ")):
            parameters = {"bridge_only": True, "limit": 6, "cluster_ids": numbers[:2] if cluster_intent else []}
            result = invoke("get_top_nodes", parameters, "Проверены узлы на межкластерных связях")
            for node in result["nodes"]:
                describe_node(node)
                observations.append(f"gid {node['gid']}: показатель межкластерного посредничества {node['metrics']['intercluster_bridge']:.3f}.")
            answer = f"Найдено {result['total']} узлов с указанными межкластерными связями; показано {len(result['nodes'])}."
        elif any(word in message for word in ("куда", "дальше", "маршрут", "цепочк")) and gid is not None:
            result = invoke("trace_money_flow", {"gid": gid, "max_depth": 3, "limit_paths": 8}, f"Прослежены исходящие маршруты от gid {gid}, до трёх шагов")
            node_link(gid)
            for path in result["paths"]:
                for route_gid in path["gids"]:
                    node_link(route_gid)
                route = " → ".join(f"gid {route_gid}" for route_gid in path["gids"])
                amounts = "; ".join(money(edge["sum_kzt"]) for edge in path["edges"])
                observations.append(f"{route}. Суммы на последовательных рёбрах: {amounts}.")
                if path["cycle"]:
                    observations.append(f"Маршрут возвращается к уже пройденному gid {path['gids'][-1]}; это наблюдаемая циклическая связь.")
            limitations.append(result["note"])
            if result["truncated"]:
                limitations.append("Показана ограниченная выборка маршрутов, полный граф может содержать другие ветки.")
            answer = f"Наблюдаемые исходящие маршруты от gid {gid}." if result["paths"] else f"У gid {gid} нет наблюдаемых исходящих маршрутов. Это не доказывает прекращение движения средств."
        elif (any(word in message for word in ("общи", "собира", "с этих", "одних")) and gids and
              any(word in message for word in ("получател", "собира", "деньги", "отправител", "источник"))):
            senders = "отправител" in message or "источник" in message
            name = "find_shared_senders" if senders else "find_common_recipients"
            result = invoke(name, {"gids": gids}, "Проверено пересечение наблюдаемых контрагентов")
            for selected_gid in gids:
                node_link(selected_gid)
            for match in result["matches"]:
                node_link(match["gid"])
                observations.append(f"gid {match['gid']}: связан с {match['n_shared']} выбранными узлами ({', '.join('gid ' + str(g) for g in match['gids'])}), сумма {money(match['sum_kzt'])}, операций {match['n_tx']}.")
            answer = ("Общие отправители" if senders else "Общие получатели") + f": найдено {result['total']}. {result['criterion']}."
            hypotheses.append("Общий контрагент может указывать на связанную структуру; экономический смысл операций требует отдельной проверки.")
        elif cluster_intent and numbers:
            cid = numbers[0]
            result = invoke("get_cluster_profile", {"cluster_id": cid}, f"Получены агрегаты кластера {cid}")
            links.append({"type": "cluster", "id": cid, "label": f"Кластер {cid}"})
            observations.append(f"Кластер {cid}: {result['n_nodes']} узлов, {result['n_seed']} seed, внутренний оборот {money(result['sum_kzt_internal'])}.")
            observations.append(f"Межкластерные потоки: входящий {money(result['inflow_external'])}, исходящий {money(result['outflow_external'])}.")
            for top_gid in result["top_gids"][:5]:
                node_link(top_gid)
            observations.append("Ключевые узлы: " + ", ".join(f"gid {g}" for g in result["top_gids"][:5]) + ".")
            hypotheses.append(result["hypothesis"])
            answer = f"Профиль кластера {cid} по наблюдаемому графу."
        elif gid is not None:
            profile = invoke("get_node_profile", {"gid": gid}, f"Прочитан профиль gid {gid}")
            describe_node(profile)
            direction = "in" if any(word in message for word in ("входящ", "отправител", "откуда")) else "out" if any(word in message for word in ("исходящ", "получател")) else "both"
            neighbors = invoke("get_neighbors", {"gid": gid, "direction": direction, "limit": 8}, f"Получены ближайшие связи gid {gid}")
            for edge in neighbors["edges"]:
                node_link(edge["src"])
                node_link(edge["dst"])
                observations.append(f"gid {edge['src']} → gid {edge['dst']}: {money(edge['sum_kzt'])}, операций {edge['n_tx']}.")
            if neighbors["truncated"]:
                limitations.append(f"Показано 8 из {neighbors['total_edges']} подходящих связей; откройте граф для продолжения.")
            answer = f"Профиль и наблюдаемые связи gid {gid}."
        else:
            role = next((name for stem, name in (("транзит", "transit"), ("собира", "consolidator"), ("консолид", "consolidator"), ("распредел", "distributor"), ("координ", "coordinator"), ("конечн", "terminal")) if stem in message), None)
            result = invoke("get_top_nodes", {"role": role, "limit": 5}, "Прочитан ранжированный список с фильтром по роли" if role else "Прочитан ранжированный список узлов")
            for node in result["nodes"]:
                describe_node(node)
            answer = f"Найдено {result['total']} узлов; показано {len(result['nodes'])} с наибольшим приоритетом."
            if not role and not any(word in message for word in ("перв", "топ", "приоритет", "начать", "важн")):
                answer = "Локальный помощник поддерживает профили, связи, маршруты, сравнение, роли, приоритеты и запросы дополнительных данных. Ниже — узлы, с которых можно начать проверку."
            if not result["nodes"]:
                observations.append("Узлов с указанной основной ролью в текущем результате нет.")
    except (ToolInputError, ValidationError) as exc:
        answer = str(exc) if isinstance(exc, ToolInputError) else "Параметры запроса не прошли проверку. Укажите существующий gid или номер кластера."
        observations = []
        hypotheses = []
        links = []

    return CopilotResponse(answer=answer, observations=observations, hypotheses=hypotheses,
                           limitations=list(dict.fromkeys(limitations)), tools_used=used, links=links)
