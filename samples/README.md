# Le vocal de démonstration

Il manque une seule chose à ce dépôt, et personne ne peut la produire à ta place :
**un vrai enregistrement**. Prends ton téléphone, enregistre-toi, et dépose le fichier ici
sous le nom `vocal_sdb.m4a`.

## En attendant : `vocal_synthese.wav`

Ce fichier (56 s, voix de synthèse Windows) est là pour **vérifier que le chemin audio
fonctionne** — dépôt du fichier, appel au fournisseur, transcription. Rien d'autre.

Ce n'est pas un vocal de démonstration et il ne faut jamais le montrer à un artisan : la
voix est parfaitement articulée, sans bruit et sans vraie hésitation. Il ne prouve donc
rien de ce que le produit sait faire. C'est un outil de test, pas une preuve.

## Pourquoi ça compte

C'est le premier geste de la démo. Un vocal lu proprement, articulé, phrase par phrase,
ne prouve rien — n'importe quel dictaphone sait le transcrire. Ce qui impressionne un
artisan, c'est de voir la machine se débrouiller d'un vocal **comme il en fait vraiment** :
en marchant, en se corrigeant, en revenant en arrière.

## Le script à lire (~90 secondes)

Ne le lis pas mot à mot. Lis-le une fois, puis raconte-le de mémoire, avec tes hésitations.

> Alors la salle de bain de… attends, c'est madame Durand, 12 rue des Lilas à Paris 11e.
> Bon. La salle de bain elle fait dans les 15 mètres carrés, faut refaire le placo sur
> deux murs parce que c'est gorgé d'eau derrière la douche, donc dépose et repose en
> hydrofuge, du BA13 vert quoi. Ensuite le carrelage au sol, alors ça c'est elle qui le
> fournit, elle l'a déjà acheté, donc moi je fais que la pose. Faut aussi que je dépose
> l'ancien carrelage, y'a du boulot là-dessus. Euh… le receveur de douche à changer aussi,
> et le mitigeur. Compte deux jours à deux pour tout ça. Ah et l'évacuation des gravats,
> faut pas que j'oublie, la dernière fois je me suis fait avoir. Voilà. Elle voudrait que
> ce soit fait avant les vacances de la Toussaint.

## Ce que ce vocal met à l'épreuve

Chaque phrase est là pour une raison — c'est ce qui rend la démo probante :

| Dans le vocal | Ce que ça teste |
|---|---|
| « attends, c'est madame Durand » | l'auto-correction en cours de phrase |
| « du BA13 vert quoi » | le jargon, souvent mal transcrit en « bat treize » |
| « c'est elle qui le fournit » | ne facturer que la pose, pas la fourniture |
| « deux jours à deux » | une durée de planning, qui ne doit pas devenir une ligne de main d'œuvre |
| « l'évacuation des gravats » | la ligne que l'artisan oublie toujours le soir |
| aucun prix cité | tout doit ressortir marqué « prix estimé » |

## Enregistrer dans de bonnes conditions

Une prise correcte, pas parfaite : un peu de bruit de fond est réaliste et joue en ta
faveur. En revanche, évite le vent direct sur le micro, qui sature la transcription.

Formats acceptés : `.m4a` (Dictaphone iPhone), `.ogg` / `.opus` (vocal WhatsApp),
`.mp3`, `.wav`. Jusqu'à 25 Mo.

## Puis fige-le en fixture

Une fois le fichier déposé, transcris-le et enregistre l'extraction. Un appel API payé
une fois, rejoué gratuitement ensuite pendant tout le développement :

```bash
uv run python scripts/enregistrer_fixture.py vocal_sdb samples/vocal_sdb.m4a --audio
```
