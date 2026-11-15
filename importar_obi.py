
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from fontes.models import FonteDados
from indicadores.models import Indicador, Periodo, ValorIndicador
from localidades.models import Localidade

from django.db import connection
import psycopg2

# Configurações da conexão já existente
config = connection.settings_dict

# Conexão de leitura com o banco original do OBI
origem = psycopg2.connect(
    dbname="obi_teste_integracao",
    user=config["USER"],
    password=config["PASSWORD"],
    host=config["HOST"],
    port=config["PORT"],
)

origem.set_session(readonly=True, autocommit=True)

cursor = origem.cursor()
cursor.execute("SELECT COUNT(*) FROM public.observacao")

print("Observações disponíveis:", cursor.fetchone()[0])

cursor.close()



# Importar as fontes utilizadas no TCC
fontes = [
    ("Banco Central do Brasil", "BCB", "https://dadosabertos.bcb.gov.br/"),
    ("Serasa", "SERASA", "https://www.serasa.com.br/"),
]

for nome, sigla, url in fontes:
    fonte, criada = FonteDados.objects.update_or_create(
        instituicao=sigla,
        defaults={
            "nome": nome,
            "url_origem": url,
            "tipo_origem": "MANUAL",
        },
    )
    print(f"Fonte: {fonte.nome} | Criada: {criada}")


# Importar os indicadores do OBI
cursor = origem.cursor()
cursor.execute("""
    SELECT i.id_indicador, i.nome, i.descricao,
           i.unidade_medida, i.periodicidade,
           i.codigo_externo, f.sigla
    FROM public.indicador i
    JOIN public.fonte f ON i.id_fonte = f.id_fonte
    WHERE f.sigla IN ('BCB', 'SERASA')
    ORDER BY i.id_indicador
""")

indicadores_origem = cursor.fetchall()
print("Indicadores encontrados:", len(indicadores_origem))

for item in indicadores_origem:
    print(item[0], item[1], "| Fonte:", item[6])



# Cadastrar os cinco indicadores no Django
for item in indicadores_origem:
    id_original, nome, descricao, unidade, periodicidade, codigo, sigla = item

    indicador, criado = Indicador.objects.update_or_create(
        nome=nome,
        defaults={
            "descricao": descricao or "",
            "unidade_medida": unidade or "Não informada",
            "periodicidade": periodicidade or "",
            "categoria": "inadimplência" if sigla == "SERASA" else "crédito",
            "ativo": True,
        },
    )

    print(f"Indicador cadastrado: {indicador.nome} | Criado: {criado}")


# Importar localidades do OBI para o Django
cursor.execute("""
    SELECT id_localidade, nome, sigla, tipo
    FROM public.localidade
    ORDER BY id_localidade
""")

localidades_origem = cursor.fetchall()

for id_original, nome, sigla, tipo in localidades_origem:
    localidade, criada = Localidade.objects.update_or_create(
        nome=nome,
        tipo=tipo,
        defaults={
            "uf": sigla if tipo == "uf" else "",
            "pais": "Brasil",
        },
    )

    print(f"Localidade: {localidade.nome} | Criada: {criada}")


# Importar os períodos mensais do OBI
cursor.execute("""
    SELECT id_periodo, data_inicio
    FROM public.periodo
    WHERE tipo_periodo = 'mensal'
    ORDER BY data_inicio
""")

periodos_origem = cursor.fetchall()

for id_original, data_inicio in periodos_origem:
    periodo, criado = Periodo.objects.update_or_create(
        ano=data_inicio.year,
        mes=data_inicio.month,
        defaults={
            "data_referencia": data_inicio,
        },
    )

    print(
        f"Período: {periodo.ano}-{periodo.mes:02d} "
        f"| Criado: {criado}"
    )


# Consultar as observações e suas categorias
cursor.execute("""
    SELECT
        o.id_observacao,
        o.id_indicador,
        o.id_localidade,
        o.id_periodo,
        o.valor,
        c.nome AS categoria
    FROM public.observacao o
    LEFT JOIN public.categoria c
        ON c.id_categoria = o.id_categoria
    ORDER BY o.id_observacao
""")

observacoes_origem = cursor.fetchall()

print("Observações preparadas:", len(observacoes_origem))


# Relacionar os indicadores originais aos indicadores do Django
mapa_indicadores = {}

for item in indicadores_origem:
    id_original = item[0]
    nome = item[1]

    indicador = Indicador.objects.get(nome=nome)
    mapa_indicadores[id_original] = indicador

print("Indicadores mapeados:", len(mapa_indicadores))


# Relacionar as localidades originais às localidades do Django
mapa_localidades = {}

for id_original, nome, sigla, tipo in localidades_origem:
    localidade = Localidade.objects.get(
        nome=nome,
        tipo=tipo
    )

    mapa_localidades[id_original] = localidade

print("Localidades mapeadas:", len(mapa_localidades))


# Relacionar os períodos originais aos períodos do Django
mapa_periodos = {}

for id_original, data_inicio in periodos_origem:
    periodo = Periodo.objects.get(
        ano=data_inicio.year,
        mes=data_inicio.month
    )

    mapa_periodos[id_original] = periodo

print("Períodos mapeados:", len(mapa_periodos))


# Validar os relacionamentos das observações
faltantes = []

for id_obs, id_ind, id_loc, id_per, valor, categoria in observacoes_origem:
    if (
        id_ind not in mapa_indicadores
        or id_loc not in mapa_localidades
        or id_per not in mapa_periodos
    ):
        faltantes.append(id_obs)

print("Observações sem relacionamento:", len(faltantes))


# Relacionar cada indicador à sua fonte no Django
mapa_fontes = {}

for item in indicadores_origem:
    id_indicador_original = item[0]
    sigla_fonte = item[6]

    fonte = FonteDados.objects.get(instituicao=sigla_fonte)
    mapa_fontes[id_indicador_original] = fonte

print("Fontes dos indicadores mapeadas:", len(mapa_fontes))


# Importar as observações sem duplicar registros
from django.db import transaction

criados = 0
atualizados = 0

with transaction.atomic():
    for id_obs, id_ind, id_loc, id_per, valor, categoria in observacoes_origem:
        metadados = {
            "id_observacao_obi": id_obs,
            "simulado": False,
        }

        if categoria is not None:
            metadados["categoria"] = categoria

        registro, criado = ValorIndicador.objects.update_or_create(
            metadados__id_observacao_obi=id_obs,
            defaults={
                "indicador": mapa_indicadores[id_ind],
                "fonte": mapa_fontes[id_ind],
                "localidade": mapa_localidades[id_loc],
                "periodo": mapa_periodos[id_per],
                "valor": valor,
                "metadados": metadados,
            },
        )

        if criado:
            criados += 1
        else:
            atualizados += 1

print("Observações criadas:", criados)
print("Observações atualizadas:", atualizados)
print("Total no Django:", ValorIndicador.objects.count())


cursor.close()
origem.close()
