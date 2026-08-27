# Devis Vocal

> Statut : **idée** — créé le 2026-08-27

## Le pitch

Générer un devis en parlant. L'utilisateur (artisan, prestataire, freelance) décrit
oralement son chantier / sa presta, et le système en sort un devis structuré,
chiffré et prêt à envoyer.

## Le problème

Faire un devis = 20-45 min derrière un écran, souvent le soir. Beaucoup d'artisans
repoussent, envoient tard, et perdent l'affaire. La saisie clavier sur mobile en
déplacement est le vrai point de friction.

## La solution (v1 pressentie)

1. **Dictée** — l'utilisateur enregistre une note vocale (sur place, dans la voiture).
2. **Transcription** — speech-to-text.
3. **Extraction** — un LLM structure : client, prestations, quantités, unités, matériaux.
4. **Chiffrage** — application du catalogue de prix / tarif horaire de l'utilisateur.
5. **Devis** — génération PDF conforme (mentions légales FR), envoi par mail/SMS.
6. **Relance** — suivi de l'ouverture + relance auto.

## Cible

- Artisans du bâtiment (plomberie, élec, peinture, menuiserie, rénovation)
- Prestataires de service à domicile
- Freelances qui devisent souvent (peu probable en priorité)

À trancher : marché de niche verticalisé (ex: plombiers) vs générique.

## À trancher

- [ ] Le nom
- [ ] Vertical unique ou générique
- [ ] Modèle éco : abonnement mensuel / au devis / freemium
- [ ] App mobile native, PWA, ou simplement un numéro WhatsApp / téléphone à appeler
- [ ] Stack : voir `docs/stack.md`
- [ ] Concurrence : voir `docs/concurrence.md`

## Structure du dossier

```
docs/          notes, recherche, specs
  brief.md         brief produit détaillé
  stack.md         choix techniques
  concurrence.md   veille concurrentielle
assets/        maquettes, captures, logos
```
