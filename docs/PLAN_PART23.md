# Part 23 --- Undo de importação de Bids

**Especificação técnica aprovada para implementação**\
**Data de referência:** 14/10/2026\
**Status:** Especificação; implementação ainda não autorizada
automaticamente por este documento.

## 1. Objetivo e limites

Implementar uma reversão administrativa, auditável e atômica de uma
importação confirmada de Bids, preservando a integridade dos dados
comerciais e o histórico de estágios.

A operação de reversão é independente do Undo do Kanban. Ela não cria
`UndoEntry`, não usa os helpers de `undo.py` e não deve chamar a rota
`delete_card`.

### Restrições obrigatórias

-   Preservar todas as alterações preexistentes e não commitadas do
    repositório.
-   Não alterar nem migrar `backend/pm.db`.
-   Não importar o relatório real da Salcomp.
-   Não alterar `.env` de servidor.
-   Não executar deploy.
-   Não fazer commit ou push sem autorização explícita.
-   Implementar somente a Part 23; aguardar revisão antes de qualquer
    etapa posterior.
-   Não executar a suíte completa do backend nesta etapa. Executar
    testes direcionados e os testes de frontend pertinentes.
-   Consultar `CLAUDE.md` e o código atual antes de editar.
-   Não presumir que o Git está limpo.

## 2. Decisões aprovadas

  -----------------------------------------------------------------------
  Tema                                Decisão
  ----------------------------------- -----------------------------------
  Reversão parcial                    Não permitida. A operação é
                                      atômica: tudo ou nada.

  Migração                            Separada, aditiva, com dry run por
                                      padrão, backup e verificação,
                                      seguindo o padrão da migração da
                                      Part 22.

  Cards criados                       Não excluir se houver atividade
                                      comercial posterior relevante ou
                                      divergência do snapshot.

  Cards atualizados                   Comparar somente campos alterados
                                      pelo lote e `column_id` quando
                                      houve movimentação; preservar os
                                      demais campos.

  Card comprovadamente excluído       Tratar como `already_deleted` e não
                                      como dependência de lote posterior,
                                      desde que exista evento `deleted`
                                      confiável.

  Card ausente sem evento `deleted`   Bloquear como
                                      `missing_without_trace`; não
                                      inferir que a exclusão foi
                                      legítima.

  `update_not_confirmed`              Registrar como item `observed`.

  Dependências entre lotes            Verificar sobreposição de
                                      `card_id`, incluindo itens
                                      `observed`; reverter primeiro o
                                      lote posterior. Cards
                                      comprovadamente excluídos ficam
                                      fora da dependência.

  Movimentação revertida              Retornar à coluna de origem, no
                                      final da coluna; não tentar
                                      recuperar a posição original.

  Repetição                           `409 already_reverted`.

  Lock SQLite                         `503 import_busy` com
                                      `Retry-After: 5` para
                                      `SQLITE_BUSY`/`SQLITE_LOCKED`.

  Foreign keys SQLite                 Não ativar `PRAGMA foreign_keys=ON`
                                      nesta parte. Não mudar engine, WAL
                                      ou `busy_timeout`.

  Ledger                              Retenção indefinida na v1; sem
                                      exclusão automática.

  Falhas recusadas                    Logs estruturados; não criar tabela
                                      de auditoria separada para
                                      tentativas recusadas.

  Undo do Kanban                      Nenhum `UndoEntry` criado pela
                                      reversão.

  Resultado da reversão               Persistir resultado e contagens no
                                      ledger; distinguir reversão com
                                      alterações comerciais de
                                      `no_commercial_changes`.
  -----------------------------------------------------------------------

## 3. Estado atual conhecido e cautelas

-   O repositório tinha aproximadamente 76 caminhos modificados ou não
    rastreados no momento do planejamento. Verificar o estado atual e
    preservar tudo.
-   `backend/pm.db` não pode ser aberto para escrita, migrado ou usado
    como banco de teste.
-   `card_import_changes` é um diff para a UI, não um ledger durável:
    pode desaparecer com a exclusão do card, não cobre cards criados e
    não cobre todos os casos sem mudança de campos.
-   `card_import_changes` e registros relacionados podem ser removidos
    pelo cascade do ORM.
-   `PRAGMA foreign_keys` está desligado no engine atual; um DELETE SQL
    cru pode deixar órfãos.
-   O alocador de IDs considera `cards` e `card_stage_events`, e os IDs
    de card não são reutilizados.
-   `card_stage_events` não tem FK para `cards` e conserva eventos
    `deleted`.
-   O SQLite usa `journal_mode=delete`, `busy_timeout=5000`,
    `synchronous=FULL` e `QueuePool`; não alterar essa configuração
    nesta parte.
-   O `BEGIN` do pysqlite é lazy: leituras anteriores à primeira escrita
    não adquirem o lock de escrita.
-   Os testes em SQLite `:memory:`/`StaticPool` não provam comportamento
    real de lock. Testes de concorrência devem usar arquivo SQLite
    temporário e duas engines/conexões independentes.

## 4. Modelo de dados aditivo

Criar somente duas tabelas novas. Não alterar DDL/modelos da Part 22.

### 4.1 `import_batch_ledgers`

Campos mínimos: - `id` - `import_batch_id`: UNIQUE, FK para
`import_batches` com CASCADE; a cascata vale para exclusão do lote, não
de cards. - `ledger_version` - `created_at` - `reverted_at`, nullable -
`reverted_by_user_id`, FK com `SET NULL` conforme padrões existentes -
Campos duráveis para o resultado final da reversão e contagens,
definidos antes da implementação. Devem distinguir pelo menos: -
resultado/outcome; - quantidade de cards excluídos; - quantidade de
cards restaurados/atualizados; - quantidade de movimentações
revertidas; - quantidade de cards já excluídos; -
`no_commercial_changes`; - resumo de conflitos/resultado quando
aplicável, sem armazenar conteúdo de células desnecessário.

A existência do ledger torna o lote elegível à reversão. Lotes
anteriores à funcionalidade, sem ledger, são `not_revertible`.

O estado de reversão só é confirmado após o `COMMIT` bem-sucedido. O
claim pode preencher temporariamente `reverted_at` dentro da transação;
qualquer recusa ou falha anterior ao commit deve fazer rollback.

### 4.2 `import_batch_items`

Campos mínimos: - `ledger_id` - `card_id`: inteiro sem FK para `cards`,
para preservar o histórico após exclusão do card. - `board_id` -
`source_key` - `action`: `created`, `updated` ou `observed` -
`observation`: nullable; preenchido apenas para `observed` -
`before_json`: nulo para `created` e `observed` - `after_json`: nulo
para `observed` - `bid_details_created` - `stage_event_max_id_after`

Restrições: - `UNIQUE(ledger_id, card_id)` - índice em `card_id` - não
criar FK de `card_id` para `cards` - itens de ledger não são apagados
quando um card é excluído.

Valores de `observation`: - `unchanged` - `older_source` -
`skipped_by_decision` - `update_not_confirmed`

Registrar `observed` para toda linha concluída do lote que resolveu para
um card existente (`existing_card_id` preenchido), mas não virou criação
nem atualização confirmada. Não registrar linhas sem card
correspondente, erros, duplicatas ou linhas ignoradas sem
correspondência.

Um lote com ledger e zero itens também é revertível como no-op. Lotes
com apenas `observed` são revertíveis como no-op, sujeitos à validação
de dependências. A operação deve registrar `no_commercial_changes`
quando apropriado, permitindo destravar lotes anteriores.

## 5. Ledger e snapshots

### 5.1 Quando gravar

`bid_import_service._write_rows` grava ledger e itens na mesma transação
da importação. Nenhum ledger deve existir para uma importação que não
tenha sido confirmada com sucesso.

-   `created`: card criado pelo lote.
-   `updated`: card existente atualizado pelo lote.
-   `observed`: decisão de importação sobre card existente sem
    atualização confirmada.

`card_import_changes` continua servindo ao diff/histórico da UI, mas não
é fonte de verdade para Undo.

### 5.2 Snapshot canônico

Serializar JSON canônico com chaves ordenadas e `ensure_ascii=False`;
datas em ISO e floats com representação estável (`repr` ou normalização
equivalente consistente).

-   `card`: todas as colunas, exceto `id`, `position`, `created_at`,
    `updated_at`. `column_id` deve estar incluído.
-   `bid_details`: marcador `present`, `id` e todas as colunas, exceto
    `created_at` e `updated_at`.
-   `source_ref`: `id` e todas as colunas, incluindo
    `last_import_batch_id` e `source_row_hash`, exceto `created_at` e
    `updated_at`.

Construir snapshots a partir das colunas reais do modelo/tabela,
evitando depender apenas de uma lista parcial de campos comerciais.

-   `before_json`: capturado antes da escrita.
-   `after_json`: capturado depois do flush.
-   `bid_details_created`: verdadeiro quando a linha não existia antes
    da operação e foi criada durante ela, inclusive pelo comportamento
    atual que pode criar uma linha vazia.
-   `stage_event_max_id_after`: maior ID de evento de estágio do card
    após o import.

A posição não faz parte do snapshot porque movimentos de outros cards
podem alterá-la sem relação com a importação.

## 6. Dependências entre lotes

Reconstruir dependências sob o lock de escrita.

Ao reverter lote A, bloquear com `later_batch_decision_on_card` se
existir lote B tal que: - `B.id > A.id`; - B está concluído; - B ainda
não foi revertido; - B tem item de ledger de qualquer ação (`created`,
`updated` ou `observed`) para um `card_id` incluído em A; - o card não
está comprovadamente excluído (evento `deleted`).

A resposta deve listar IDs dos lotes posteriores e os cards
relacionados. A regra usa `card_id`, nunca `source_key`.

Consequências: - Reverter lotes posteriores primeiro. - Um lote
posterior que apenas observou um card também cria dependência. - Um lote
falho ou pendente não bloqueia. - Card excluído e reimportado tem outro
ID; o card novo não pertence ao ledger antigo. - A decisão de um lote
posterior sobre card comprovadamente excluído não bloqueia a reversão do
lote anterior. - Um lote com apenas `observed` pode ser revertido como
no-op e liberar a reversão anterior.

## 7. Regras de reversão e conflitos

### 7.1 Cards criados pelo lote

Excluir somente quando todos os critérios forem satisfeitos: - estado
comercial atual coincide com `after_json` (excluída a posição); -
`column_id` coincide; - não existe edição em `card_edit_history`; - não
existe atividade em `card_actions`; - não existe evento de estágio
posterior a `stage_event_max_id_after`; - não há lote posterior não
revertido que registre decisão sobre o card.

Qualquer alteração comercial posterior, atividade relevante, movimento
ou dependência posterior bloqueia a reversão do lote. Não excluir
automaticamente um card que recebeu atividade comercial posterior, mesmo
se tiver retornado ao estado original.

-   Card ausente com evento `deleted`: `already_deleted`, resolvido sem
    escrita.
-   Card ausente sem evento `deleted`: bloqueio `missing_without_trace`.

A exclusão pode acionar os cascades do ORM para detalhes e referências.
Os itens de ledger precisam sobreviver.

### 7.2 Cards atualizados pelo lote

A decisão é por card: ou todas as alterações aplicáveis ao card são
revertidas, ou nenhuma delas é aplicada.

Para cada campo que o lote alterou: - valor atual == valor `new`:
restaurar `old`; - valor atual == valor `old`: já está revertido; não
escrever esse campo; - valor atual diferente de `old` e `new`: conflito
`field_modified_since`.

Campos que o import não alterou permanecem como estão.

Movimentação: - se a coluna atual é a coluna nova registrada no lote,
voltar à coluna antiga no final da coluna; - se a coluna atual é outra,
conflito `moved_since`; - posição anterior exata não é restaurada.

`source_ref` deve restaurar o estado anterior, incluindo
`last_import_batch_id` e `source_row_hash`.

### 7.3 `bid_details`

Se a importação criou a linha: - guardar o ID e snapshot completo da
linha em `after_json`; - excluir a linha na reversão somente se ela
ainda existir com o mesmo ID e continuar idêntica ao snapshot
pós-import; - se foi alterada posteriormente, bloquear com
`bid_details_modified_after_import`; - se estiver ausente enquanto o
card existe, bloquear como inconsistência (`missing_without_trace` ou
código específico equivalente).

Se a linha já existia: - restaurar apenas os campos de `bid_details` que
o lote alterou; - preservar campos não alterados; - detectar conflitos
campo a campo; - bloquear o card inteiro se qualquer campo relevante
tiver conflito.

### 7.4 Cards atualizados excluídos posteriormente

Se o card está ausente e há evento `deleted`, tratar como
`already_deleted`, sem recriar automaticamente o card. Se estiver
ausente sem evento, bloquear.

### 7.5 Atomicidade

Qualquer conflito bloqueia a reversão inteira. Não há opção de ignorar
cards individualmente nem de reversão parcial na v1.

## 8. Fingerprint e fluxo de endpoints

### 8.1 Preview

`GET /api/imports/{id}/revert-preview`

-   Apenas admin, com rate limit de importação.
-   Operação somente de leitura.
-   Devolve elegibilidade, plano, contagens, bloqueadores, lotes
    dependentes e `plan_fingerprint`.
-   Deve indicar se a operação é `no_commercial_changes`.
-   Lotes sem ledger: `not_revertible`.
-   Lotes falhos/pendentes: não revertíveis.

O fingerprint é SHA-256 do JSON canônico com: - versão do fingerprint; -
`batch_id`; - `ledger_version`; - lista ordenada dos itens; - `item_id`,
`card_id`, ação e classificação; - hash do snapshot atual ou marcador
`absent`; - marcadores de atividade; - lotes dependentes; - operações
planejadas.

Não incluir timestamps voláteis nem nomes de usuários. O fingerprint não
precisa ser assinado porque o servidor sempre reconstrói o plano e o
cliente não pode impor o resultado; ele serve para detectar plano
obsoleto.

### 8.2 Confirmação

`POST /api/imports/{id}/revert`, corpo `{ "plan_fingerprint": "..." }`
(ou nome equivalente, consistente entre schema e frontend).

Requisitos: - apenas admin e rate limit; - uma única transação; - sessão
limpa (`new`, `dirty`, `deleted` vazios) antes da operação.

Sequência: 1. Primeira escrita: claim condicional no ledger:
`UPDATE import_batch_ledgers SET reverted_at=:now, reverted_by_user_id=:user WHERE import_batch_id=:batch AND reverted_at IS NULL`.
2. Se `rowcount == 0`, rollback e releitura: - lote inexistente: 404; -
sem ledger: 409 `not_revertible`; - já revertido: 409
`already_reverted`. 3. Reconstruir o plano sob o lock de escrita. 4.
Fingerprint divergente: rollback e 409 `plan_changed`. 5. Qualquer
bloqueador: rollback e 409 `revert_blocked`, com códigos e referências
necessárias. 6. Executar reversão sob
`stage_context(db, user, "import_revert")`. 7. Verificação pós-escrita
dentro da mesma transação. 8. Persistir resultado e contagens duráveis
no ledger. 9. Commit único. 10. Só responder 200 após `commit()`
retornar sem exceção.

A implementação deve usar um único escritor de reversão em
`bid_import_revert.py`. A rota cuida de autenticação, validação do
token/fingerprint e mapeamento de erros HTTP, sem duplicar a lógica
transacional.

## 9. Concorrência e falhas SQLite

Não alterar o engine, não ativar WAL, não alterar `busy_timeout` e não
ativar foreign keys nesta parte.

A primeira escrita adquire o lock antes das leituras decisórias.
Recalcular o plano e executar a reversão na mesma transação.

-   `SQLITE_BUSY`/`SQLITE_LOCKED` no claim/início: rollback e
    `503 import_busy`, `phase: start`, `applied: false`,
    `Retry-After: 5`.
-   Lock durante preview: `503 import_busy`, sem escrita.
-   Lock durante o COMMIT: fazer rollback explícito; não repetir o
    commit.
-   Depois de falha de commit, usar sessão nova para consultar o estado
    durável:
    -   ledger não revertido: `503 import_busy`, `phase: commit`,
        `applied: false`, `Retry-After: 5`;
    -   estado revertido ou impossível de confirmar:
        `500 revert_outcome_unconfirmed`; nunca afirmar sucesso sem
        confirmação do commit.
-   Outros `OperationalError` não relacionados a lock: 500, com
    rollback.
-   Se rollback falhar, descartar a conexão.
-   Qualquer exceção durante execução/verificação provoca rollback
    integral.

Testes de lock devem usar duas engines/conexões em banco SQLite
temporário em arquivo; SQLite em memória com `StaticPool` não é prova
suficiente.

## 10. Verificação pós-escrita

Executar dentro da mesma transação, usando `expire_all()` e consultas
novas. Os hashes "antes" são calculados sob o lock, antes de escrever.

### 10.1 Integridade comercial

Hash comercial por card: - todas as colunas de `cards`, exceto `id`,
`position`, `created_at`, `updated_at`; - snapshots de `bid_details` e
`source_ref`, exceto timestamps; - `column_id` incluído, com exceção das
mudanças de coluna planejadas.

Verificar: - cards fora do plano mantêm hash comercial idêntico; - cards
atualizados restauram os campos alterados para `before_json`; - todos os
demais campos dos cards atualizados permanecem iguais ao estado
imediatamente anterior à reversão; - coluna volta à origem somente nos
casos de movimentação revertida; - `bid_details` criado pelo lote é
removido; registros preexistentes têm apenas campos afetados
restaurados; - `source_ref` é restaurado, incluindo hash da linha e lote
anterior; - cards `observed` não são alterados; - contagens/deltas de
`cards`, `card_bid_details` e `card_source_refs` correspondem ao plano.

### 10.2 Integridade estrutural do Kanban

-   Não há órfãos nas tabelas relacionadas para os IDs afetados:
    `card_bid_details`, `card_source_refs`, `card_import_changes`,
    `card_edit_history`, `card_actions`.
-   Todo card restante do plano referencia coluna existente do mesmo
    board.
-   Em cada coluna afetada, a sequência dos cards fora do plano,
    ordenada por `(position, id)`, permanece idêntica antes e depois.
-   Cards cuja movimentação foi revertida ficam no final da coluna de
    origem, com posições maiores que as dos demais cards e distintas
    entre si.
-   Não exigir posições contíguas; exclusões atuais já podem deixar
    lacunas.
-   `undo_entries` permanece idêntica antes e depois.
-   O ledger fica marcado como revertido somente dentro da transação; a
    confirmação só é considerada concluída depois do commit.

Qualquer divergência provoca rollback integral.

## 11. Histórico e interface

### Backend

-   Novo `backend/bid_import_revert.py`: planejamento e execução.
-   Novo `backend/migrate_import_revert.py`: migração aditiva, dry run
    por padrão, backup, verificação, recusa de aplicação dupla e recusa
    se a migração-base exigida estiver ausente.
-   `import_routes.py`: endpoints administrativos de preview e
    confirmação.
-   `bid_import_service.py`: escrita de ledger e itens (`created`,
    `updated`, `observed`) na transação da importação.
-   `stage_history.py`: nova origem `import_revert`.
-   `import_schema.py`: segundo grupo de tabelas e estado de migração
    próprio.
-   `database.py`: guarda `ImportRevertMigrationRequired` antes de
    `create_all`.
-   `create_commercial_db.py` e verificações de banco passam a
    reconhecer as tabelas novas.
-   Não alterar os modelos/DDL da Part 22.

### Persistência e auditoria

Persistir no ledger o resultado e as contagens finais. Para tentativas
recusadas/falhas, emitir logs estruturados no logger
`bid_import_revert`, sem conteúdo de células/títulos e limitando IDs de
cards por evento. Não criar tabela separada para recusas na v1.

### Frontend

-   `BidImportHistory.tsx`: mostrar badge `Reverted`, responsável, data
    e resultado/contagens.
-   Mostrar `Revert import…` somente quando o lote for revertível e
    ainda não estiver revertido.
-   Novo `BidImportRevertDialog`: resumo de cards excluídos, campos
    restaurados, movimentações revertidas e cards já excluídos; tabela
    de conflitos com card, campo, valor importado, valor atual, motivo e
    orientação.
-   Desabilitar confirmação quando houver bloqueadores.
-   Informar que a operação não pode ser desfeita pelo Undo do Kanban e
    que reaplicar exige importar o arquivo novamente.
-   Mostrar `no_commercial_changes` de forma distinta de uma reversão
    que alterou dados comerciais.
-   O lote revertido deve ter estado visual próprio, sem reutilizar o
    verde de `completed`.
-   Atualizar tipos e chamadas em `frontend/src/lib/api.ts` e
    `frontend/src/lib/bidImport.ts`.

## 12. Migração

Novo `migrate_import_revert.py --database <path>`: - dry run por
padrão; - `--apply` cria apenas as duas tabelas novas; - backup; -
verificação de preservação das linhas existentes; - recusa segunda
aplicação; - recusa banco sem a migração-base exigida; - não modifica as
tabelas da Part 22. - `import_schema.py` mantém estado próprio para o
novo grupo, para não marcar como incompatível um banco que já tenha a
Part 22 mas ainda não tenha a Part 23. - `database.py` bloqueia
inicialização se as novas tabelas forem obrigatórias e estiverem
ausentes, antes de `create_all`. - `create_commercial_db.py` cria o
esquema novo para bancos comerciais recém-criados. - Não executar
migração em `backend/pm.db`.

## 13. Testes exigidos

### Backend unitário/integração

-   Planejamento para cards criados, atualizados e observados.
-   Card criado com `deal_value` alterado depois da importação.
-   Edição de admin e de membro depois do import.
-   Movimento e atividade posterior.
-   Card excluído com evento `deleted` e ausente sem evento.
-   Campo atual igual a `old`, igual a `new` e diferente de ambos.
-   `bid_details` inexistente antes e criado durante update.
-   `bid_details` alterado depois, ausente inesperadamente e
    preexistente.
-   `source_row_hash` e `last_import_batch_id` restaurados.
-   Reimport do mesmo arquivo depois da reversão volta a propor a
    atualização esperada.
-   Dependência A→B para `created`, `updated` e `observed`, inclusive
    `unchanged` e `update_not_confirmed`.
-   Reversão de B libera A.
-   Card comprovadamente excluído não cria dependência; ausência sem
    evento bloqueia.
-   Lote com zero itens e lote somente `observed` como no-op.
-   Reversão duplicada.
-   Alteração entre preview e confirmação gera `409 plan_changed`.
-   Atomicidade com falha injetada no meio da execução e na verificação
    pós-escrita.
-   Erros de lock no início e no COMMIT usando duas conexões em arquivo
    temporário.
-   Resultado e contagens persistidos.
-   Migração dry run, aplicação, aplicação dupla, backup, rollback e
    guard de inicialização.
-   Sem `UndoEntry` criada e `undo_entries` inalterada.
-   Compatibilidade com testes de importação e Undo já existentes.

### Frontend

Vitest para diálogo com/sem conflito, botão desabilitado, estados `409`,
`503`, já revertido e `no_commercial_changes`.

### Playwright

Novo `bid-import.revert.spec.ts`: - Importar, reverter e verificar
cards/histórico. - Edição manual posterior causa conflito sem alteração
de dados. - Duplo clique/segunda confirmação tratados corretamente. -
Membro não vê o botão e API recusa. - Lotes dependentes e `observed`. -
Usar apenas dados sintéticos E2E. Limpeza deve apagar somente os cards
sintéticos conhecidos por código/IDs através da API administrativa
existente. - Executar também testes de regressão necessários, incluindo
os testes de importação e dashboard pertinentes.

## 14. Riscos residuais conhecidos

1.  Foreign keys continuam desligadas; DELETE cru pode deixar órfãos. A
    reversão detecta casos sem trilha e não tenta repará-los
    automaticamente.
2.  Um COMMIT pode ter resultado ambíguo para o cliente; nunca declarar
    sucesso sem confirmação durável.
3.  Reimportações posteriores que apenas observam um card podem bloquear
    reversões anteriores até que o lote posterior seja revertido.
4.  O Undo de exclusão do Kanban recria cards com novo ID e não restaura
    `bid_details`/`source_ref`; a Part 23 não corrige essa dívida.
5.  Eventos de estágio são append-only; verificar que os KPIs
    interpretam corretamente baseline seguido de `deleted`.
6.  O ledger guarda dados comerciais anteriores/posteriores
    indefinidamente; não incluí-los em logs.
7.  Excluir um board elimina seus lotes e ledgers conforme a política de
    cascata existente.
8.  Posições podem ter lacunas depois da reversão.

## 15. Critérios de conclusão

A Part 23 só está pronta para revisão quando: - implementação e migração
forem aditivas; - todos os testes direcionados executados forem
reportados com resultado; - testes de concorrência usarem arquivo SQLite
temporário e conexões independentes; - não houver alterações em
`backend/pm.db`, `.env` de servidor, commit, push ou deploy; - as
alterações preexistentes do Git estiverem preservadas; - o relatório
listar arquivos alterados, migração, testes, riscos e limitações; -
nenhuma etapa subsequente for iniciada sem revisão explícita.
