"""Can source-only intended-motion templates adapt an unseen person's EMG?

Offline proxy, not a glove-free acquisition trial: DB2 S1 did not follow these
new templates. Original cue times determine phase; all eligible repetitions
supervise adaptation, with no support/query or validation partition. Reported
template tracking is training-data replay, not held-out generalization. No
target glove enters templates, phase alignment, normalizer fitting, or the
weak-label training tensors. Measured glove scores are separate diagnostics.
"""
from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import numpy as np
import run_db2_paper_cfc_finetune as paper
import torch
from scipy.io import loadmat
from train import CfCTrainingConfig, apply_target_normalizer

# Agent-chosen fixed grid: 2 s pre-cue, 5 s action, 2 s return/rest for playback.
# These are timing cues, not claims about anatomical movement sub-phases.
PHASE = np.arange(-40, 141, dtype=float) / 100  # Exact zero/one cue boundaries.
FS = 2000


def cue_segments(stimulus, repetition):
    """Map each positive (action, repetition) to one original cue interval."""
    actions, reps = np.asarray(stimulus).ravel(), np.asarray(repetition).ravel()
    if not len(actions) or actions.shape != reps.shape:
        raise ValueError("cue labels must be nonempty and aligned")
    boundaries = np.r_[0, np.flatnonzero(
        (actions[1:] != actions[:-1]) | (reps[1:] != reps[:-1])) + 1, len(actions)]
    result = {}
    for start, end in pairwise(boundaries):
        if actions[start] <= 0:
            continue
        key = int(actions[start]), int(reps[start])
        if key[1] <= 0 or key in result:
            raise ValueError(f"invalid or noncontiguous cue {key}")
        result[key] = int(start), int(end)
    return result


def balanced_templates(curves, subjects, actions, held_out):
    """Median six repetitions/person, then median people (equal person weight)."""
    if not subjects or held_out.upper() in {s.upper() for s in subjects}:
        raise ValueError("held-out subject must be excluded from template sources")
    expected = {(s, a, r) for s in subjects for a in actions for r in range(1, 7)}
    if len(set(subjects)) != len(subjects) or set(curves) != expected:
        raise ValueError("template sources must have exactly six repetitions per action")
    repetitions = np.asarray([[[curves[s, a, r] for r in range(1, 7)]
                               for a in actions] for s in subjects], dtype=np.float32)
    if repetitions.shape != (len(subjects), len(actions), 6, len(PHASE), 10):
        raise ValueError("template curves must follow the phase/J10 contract")
    counts = np.isfinite(repetitions).sum(axis=2)
    core = (PHASE >= 0) & (PHASE < 1)  # Cue end is exclusive: phase 1 is first rest sample.
    # Recording ends can truncate the last repetition's return/rest context.
    # Never extrapolate it: require all six active cues and >=5 context curves.
    if not (counts[:, :, core] == 6).all() or (counts < 5).any():
        raise ValueError("insufficient finite source phase coverage")
    within = np.nanmedian(repetitions, axis=2)
    if within.shape != (len(subjects), len(actions), len(PHASE), 10):
        raise ValueError("template curves must follow the phase/J10 contract")
    if not np.isfinite(within).all():
        raise ValueError("template curves contain nonfinite values")
    return np.median(within, axis=0), within


def build_source_templates(files, protocol, output):
    subjects, actions = protocol['source_subjects'], protocol['actions']
    if protocol['target_subject'].upper() in {s.upper() for s in subjects}:
        raise ValueError("target cannot be read as a template source")
    curves, manifest = {}, []
    for subject in subjects:
        for exercise in protocol['exercises']:
            path = files[f'{subject}_{exercise}_A1.mat']
            data = loadmat(path, variable_names=['glove', 'stimulus', 'repetition'])
            glove = data['glove']
            if glove.ndim != 2 or glove.shape[1] != 22:
                raise ValueError(f"invalid DB2 glove layout: {path}")
            cues = cue_segments(data['stimulus'], data['repetition'])
            if len(data['stimulus']) != len(glove):
                raise ValueError(f"cue/glove length mismatch: {path}")
            for (action, rep), (start, end) in cues.items():
                if action not in actions:
                    continue
                positions = start + PHASE * (end - start)
                valid = (positions >= 0) & (positions <= len(glove) - 1)
                bounded = np.clip(positions, 0, len(glove) - 1)
                lower = np.floor(bounded).astype(int)
                upper = np.ceil(bounded).astype(int)
                fraction = (positions - lower)[:, None]
                columns = list(paper.JOINT_ANGLES10_INDICES)
                curve = (glove[lower][:, columns] * (1 - fraction)
                         + glove[upper][:, columns] * fraction)
                curve[~valid] = np.nan  # Missing recording context is not a held pose.
                key = subject, action, rep
                if key in curves:
                    raise ValueError(f"duplicate source repetition: {key}")
                curves[key] = curve.astype(np.float32)
            manifest.append({'subject': subject, 'file': str(path.resolve()),
                             'size_bytes': path.stat().st_size,
                             'glove_sha256': paper._sha256_array(glove),
                             'cue_sha256': paper._sha256_json(
                                 {str(k): v for k, v in cues.items()})})
        print(f'Templates: {subject}, {len(curves)} source repetitions', flush=True)
    public, within = balanced_templates(curves, subjects, actions, protocol['target_subject'])
    coverage = np.asarray([[[np.isfinite(curves[s, a, r]).all(axis=1)
                            for r in range(1, 7)] for a in actions] for s in subjects]).sum(axis=2)
    # Display-only sensor coordinate, NOT an anatomical flexion/degree calibration.
    low, high = np.quantile(within.reshape(-1, 10), [0.05, 0.95], axis=0)
    display = np.clip((public - low) / np.maximum(high - low, 1e-6), 0, 1)
    playback_time = (PHASE + 0.4) * 5
    np.savez_compressed(output / 'templates.npz', actions=actions, phase=PHASE,
                        raw_j10=public, source_subjects=subjects, per_subject=within,
                        repetition_coverage=coverage,
                        q10=np.quantile(within, .1, axis=0),
                        q90=np.quantile(within, .9, axis=0),
                        playback_time_s=playback_time, display_sensor_unit=display,
                        display_low=low, display_high=high)
    metadata = {'source_subjects': subjects, 'excluded_subject': protocol['target_subject'],
                'actions': actions, 'n_repetitions': len(curves),
                'aggregation': 'median_6_repetitions_then_median_subjects_raw_J10',
                'context_missing_policy': 'no extrapolation; all 6 required at phase [0,1), >=5 elsewhere',
                'min_repetition_coverage': int(coverage.min()),
                'incomplete_context_phase_cells': int((coverage < 6).sum()),
                'phase': 'original_stimulus_onset=0_offset=1; no glove-based warp',
                'template_sha256': paper._sha256_array(public),
                'phase_sha256': paper._sha256_array(PHASE), 'source_files': manifest,
                'target_contract': paper.resolve_target_contract('joint_angles10'),
                'display_warning': 'source percentile sensor coordinate, not physical flexion or degrees',
                'playback': {'duration_s': 9, 'cue_on_s': 2, 'cue_off_s': 7,
                             'time_s': playback_time, 'raw_j10': public,
                             'display_sensor_unit': display,
                             'display_low': low, 'display_high': high}}
    (output / 'templates.json').write_text(json.dumps(paper.make_jsonable(metadata), indent=2), encoding='utf-8')
    return public, metadata


def weak_stream_targets(stream, score_mask, templates, actions, cues_by_file):
    """Construct labels using only cue metadata, row provenance, and source templates."""
    mask = np.asarray(score_mask, dtype=bool)
    if mask.shape != (len(stream.x),) or not mask.any():
        raise ValueError("adaptation mask must select stream rows")
    if np.asarray(templates).shape != (len(actions), len(PHASE), 10):
        raise ValueError("invalid public template dimensions")
    if not np.isfinite(templates).all() or len(set(actions)) != len(actions):
        raise ValueError("invalid public templates/actions")
    # Deliberately do not even copy stream.y: target glove values cannot enter.
    labels = np.full((len(stream.x), 10), np.nan, dtype=np.float32)
    phases = np.full(len(stream.x), np.nan)
    files = stream.source_recording_ids
    if files is None or stream.action_labels is None or stream.repetition_labels is None:
        raise ValueError("original recording/action/repetition provenance is required")
    lookup = {int(a): i for i, a in enumerate(actions)}
    for file in np.unique(files[mask]):
        for action, rep in set(zip(stream.action_labels[mask & (files == file)],
                                   stream.repetition_labels[mask & (files == file)])):
            rows = np.flatnonzero(mask & (files == file) & (stream.action_labels == action)
                                 & (stream.repetition_labels == rep))
            start, end = cues_by_file[str(file)][int(action), int(rep)]
            if end <= start:
                raise ValueError("invalid cue duration")
            phase = (stream.alignment_indices[rows].astype(float) - start) / (end - start)
            if phase.min() < PHASE[0] or phase.max() > PHASE[-1]:
                raise ValueError(f"adaptation row outside source template context: {file}/{action}/{rep}")
            phases[rows] = phase
            for j in range(10):
                labels[rows, j] = np.interp(phase, PHASE, templates[lookup[int(action)], :, j])
    if not np.isfinite(labels[mask]).all() or not np.isnan(labels[~mask]).all():
        raise ValueError("weak-label coverage/isolation failure")
    return labels, phases


def evaluate_arm(model, stream, score_mask, y_stats, device, output, name, *, measured_glove):
    """Replay adaptation rows against the intended template, not a held-out set."""
    measured_glove = np.asarray(measured_glove, dtype=np.float32)
    if measured_glove.shape != (int(np.sum(score_mask)), 10) or not np.isfinite(measured_glove).all():
        raise ValueError('measured glove diagnostic must align with scored rows')
    result = paper.evaluate_chain(model, stream, target_stats=y_stats,
                                  device=device, score_mask=score_mask)
    truth, prediction = result.pop('y_true'), result.pop('y_pred')
    result.update(reference='public_template', data_usage='adaptation_training_rows_no_holdout')
    result['per_action_metrics'] = paper.compute_grouped_regression_metrics(
        truth, prediction, stream.action_labels[score_mask])
    result['measured_glove_diagnostic'] = {
        'reference': 'measured_glove_not_intended_control_target',
        'data_usage': 'adaptation_training_rows_no_holdout',
        'metrics': paper.compute_regression_metrics(measured_glove, prediction),
        'per_action_metrics': paper.compute_grouped_regression_metrics(
            measured_glove, prediction, stream.action_labels[score_mask])}
    np.savez_compressed(output / f'{name}_training_replay.npz', y_template=truth, y_pred=prediction,
                        y_measured_glove=measured_glove,
                        action=stream.action_labels[score_mask],
                        repetition=stream.repetition_labels[score_mask],
                        time_s=stream.time_s[score_mask],
                        alignment_indices=stream.alignment_indices[score_mask],
                        recording=stream.source_recording_ids[score_mask])
    print(f'{name} template tracking (training rows, no holdout): MAE={result["metrics"]["mae_mean"]:.6f}, '
          f'R2={result["metrics"]["r2_mean"]:.6f}', flush=True)
    return result


def run_experiment(args):
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'will not overwrite nonempty experiment: {output}')
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
    if 'adaptation_provenance' in checkpoint or 'template_adaptation_provenance' in checkpoint:
        raise ValueError('requires source-pretrained, not target-adapted checkpoint')
    config = CfCTrainingConfig(**checkpoint['config'])
    if config.target_mapping != 'joint_angles10' or not config.stateful:
        raise ValueError('this experiment requires stateful J10 pretraining')
    protocol = copy.deepcopy(checkpoint['resume_integrity']['protocol'])
    target, sources = protocol['target_subject'], protocol['source_subjects']
    actions, exercises = tuple(protocol['actions']), tuple(protocol['exercises'])
    files = {p.name: p for p in paper.list_db2_files(config.db2_dir)}
    templates, template_metadata = build_source_templates(files, protocol, output)
    selected_sources, streams, masks, actual_plan = [], [], [], {}
    for subject in sources:
        selected, stream = paper.load_subject_splits(
            subject, exercises, config, actions=actions,
            cache_dir=config.db2_dir / '.feature_cache')
        selected_sources.append(selected)
        streams.append(stream)
        masks.append(paper.exact_provenance_score_mask(stream, selected))
        actual_plan[subject] = paper.describe_all_selected_repetitions(selected, actions=actions)
        print(f'Features: {subject}, {len(stream.x)} stream rows', flush=True)
    if actual_plan != protocol['source_supervision_plan']:
        raise ValueError('source supervision differs from pretrain protocol')
    source_selected, source_stream = paper.concat_splits(selected_sources), paper.concat_splits(streams)
    source_mask = np.concatenate(masks)
    del selected_sources, streams, masks, selected, stream
    expected = paper.build_resume_integrity_metadata(
        x_stats=checkpoint['feature_normalization_stats'],
        y_stats=checkpoint['target_normalization_stats'],
        source_supervised=source_selected, source_stream=source_stream,
        source_stream_score_mask=source_mask, protocol=protocol)
    x_stats, y_stats = paper.validate_resume_integrity_metadata(
        checkpoint, expected, allow_legacy_resume_without_integrity_binding=False)
    n_source_selected = len(source_selected.x)
    del source_selected
    selected, target_stream = paper.load_subject_splits(
        target, exercises, config, actions=actions, cache_dir=config.db2_dir / '.feature_cache')
    # The original checkpoint binding was validated above, unchanged. The user
    # removed target holdouts for adaptation; do not reuse its historical 4/2 plan.
    protocol.pop('target_support_repetitions_per_action', None)
    protocol.pop('target_support_query_plan', None)
    protocol.update(target_partition='none', target_supervision_plan=
                    paper.describe_all_selected_repetitions(selected, actions=actions))
    adaptation_mask = paper.provenance_score_mask(target_stream, selected)
    if int(adaptation_mask.sum()) != len(selected.x):
        raise ValueError('not all selected target rows are supervised')
    cues_by_file = {}
    for exercise in exercises:
        filename = f'{target}_{exercise}_A1.mat'
        # This load cannot see glove; labels retain their original sample clock.
        cues = loadmat(files[filename], variable_names=['stimulus', 'repetition'])
        cues_by_file[filename] = cue_segments(cues['stimulus'], cues['repetition'])
    weak_y, phases = weak_stream_targets(target_stream, adaptation_mask, templates, actions, cues_by_file)
    poisoned = replace(target_stream, y=np.full_like(target_stream.y, np.nan))
    check_y, _ = weak_stream_targets(poisoned, adaptation_mask, templates, actions, cues_by_file)
    np.testing.assert_array_equal(weak_y, check_y)
    np.savez_compressed(output / 'adaptation_labels.npz', weak_y=weak_y[adaptation_mask],
                        phase=phases[adaptation_mask], action=target_stream.action_labels[adaptation_mask],
                        repetition=target_stream.repetition_labels[adaptation_mask],
                        alignment_indices=target_stream.alignment_indices[adaptation_mask],
                        recording=target_stream.source_recording_ids[adaptation_mask])
    source_norm = paper.normalize_sequence_inputs(source_stream, x_stats=x_stats, y_stats=y_stats)
    target_norm = paper.normalize_sequence_inputs(target_stream, x_stats=x_stats, y_stats=y_stats)
    weak_norm = replace(target_norm, y=apply_target_normalizer(weak_y, y_stats))
    real_y = np.full_like(target_norm.y, np.nan)
    real_y[adaptation_mask] = target_norm.y[adaptation_mask]
    real_norm = replace(target_norm, y=real_y)
    measured_glove = target_stream.y[adaptation_mask]
    summary = {
        'question': 'source-only motion templates as intended weak labels for unseen-subject ATL',
        'schema_version': 2, 'status': 'running', 'checkpoint': str(args.checkpoint.resolve()),
        'checkpoint_sha256': hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
        'config': checkpoint['config'], 'protocol': protocol,
        'template_sha256': template_metadata['template_sha256'],
        'training': {'epochs': args.epochs, 'dd_lr': args.lr, 'cfc_atl_lr': args.lr,
                     'subject_weight': args.subject_weight, 'paired_seed': args.seed,
                     'selection': 'fixed_final_epoch_no_model_selection',
                     'rationale': '60 epochs/w10 match prior real-label run; both lr3e-5 per user'},
        'counts': {'source_supervised': n_source_selected, 'source_stream': len(source_stream.x),
                   'target_adaptation': int(adaptation_mask.sum()),
                   'target_context_only': int((~adaptation_mask).sum()),
                   'target_stream': len(target_stream.x)},
        'integrity': {'source_checkpoint_binding_verified': True,
                      'target_glove_poisoning_invariance_passed': True,
                      'pretrain_resume_protocol_identity': expected['protocol_identity'],
                      'all_selected_target_rows_supervised': True,
                      'target_input_identity': paper.stream_input_provenance_identity(target_stream),
                      'adaptation_mask_identity': paper._sha256_array(adaptation_mask),
                      'weak_training_targets_identity': paper._sha256_array(weak_norm.y),
                      'cue_identity': paper._sha256_json(
                          {f: {str(k): v for k, v in cs.items()} for f, cs in cues_by_file.items()})},
        'evaluation_protocol': {
            **paper.chain_evaluation_protocol_metadata(),
            'input_context': 'all chronological windows; original stream/reset boundaries unchanged',
            'score_rows': 'all eligible target adaptation rows, without repetition holdouts',
            'reference': 'public_template; measured glove is a separate diagnostic',
            'data_usage': 'adaptation_training_rows_no_holdout',
            'selection_role': 'fixed final epoch; no validation or test-based model selection'},
        'limitations': [
            'S1 did not follow the new 9-second template; this is a retrospective cue-phase proxy.',
            'All selected target repetitions supervise adaptation; there is no query or validation holdout.',
            'Template tracking is training-data fit, not evidence of held-out or live control performance.',
            'Rest/boundary eligibility masks and TBPTT truncation are unchanged; not every raw sample has a loss.',
            'Refined DB2 annotations define eligible adaptation rows; no-glove online segmentation is unvalidated.',
            'J10 values are raw sensor units, not calibrated joint degrees.',
            'The target is excluded from source pretraining/templates, but all its eligible repetitions are adapted.',
            'One intact subject and one seed, not evidence of amputee/general population performance.',
            'Existing float32 R2 convention retained; near-constant per-action targets may make R2 unstable.',
            'No score-based hyperparameter/epoch/action selection; per-action ranks are descriptive only.'],
        'results': {}}
    del source_stream, selected, poisoned, check_y
    gc.collect()
    device = paper.resolve_device(config.device)
    model = paper.build_cfc_regressor(input_dim=source_norm.x.shape[-1], output_dim=10,
                                      hidden_units=config.hidden_units, model_family=config.model_family,
                                      cfc_dropout=config.cfc_dropout,
                                      cfc_backbone_layers=config.cfc_backbone_layers,
                                      cfc_backbone_dropout=config.cfc_backbone_dropout).to(device)
    model.load_state_dict(checkpoint['model_state_dict'])
    base_weights = {k: v.detach().clone() for k, v in model.state_dict().items()}
    summary['results']['zero_shot'] = evaluate_arm(
        model, weak_norm, adaptation_mask, y_stats, device, output, 'zero_shot', measured_glove=measured_glove)
    paper.save_summary(output, summary)
    for name, train_stream in [('weak_template', weak_norm), ('real_glove', real_norm)]:
        print(f'Start {name}: fixed {args.epochs} epochs, lr={args.lr}, seed={args.seed}', flush=True)
        paper.set_random_seed(args.seed)
        adapted, history, audit = paper.fine_tune_head(
            model=model, support_split=train_stream, config=config, y_stats=y_stats,
            device=device, learning_rate=args.lr, epochs=args.epochs, enable_atl=True,
            source_split=source_norm, source_score_mask=source_mask,
            support_score_mask=adaptation_mask, dd_lr=args.lr, cfc_atl_lr=args.lr,
            atl_subject_weight=args.subject_weight)
        if not all(np.isfinite(list(entry.values())).all() for entry in history):
            raise ValueError(f'nonfinite training losses: {name}')
        for key, value in model.state_dict().items():
            torch.testing.assert_close(value, base_weights[key], rtol=0, atol=0)
        torch.save({'model_state_dict': adapted.state_dict(), 'config': checkpoint['config'],
                    'target_contract': checkpoint['target_contract'],
                    'feature_normalization_stats': x_stats, 'target_normalization_stats': y_stats,
                    'history': history, 'audit': audit,
                    'template_adaptation_provenance': {'arm': name, 'training': summary['training'],
                        'base_sha256': summary['checkpoint_sha256'], 'integrity': summary['integrity'],
                        'template_sha256': summary['template_sha256'], 'protocol': protocol}},
                   output / f'{name}_final.pt')
        summary['results'][name] = evaluate_arm(
            adapted, weak_norm, adaptation_mask, y_stats, device, output, name, measured_glove=measured_glove)
        summary['results'][name].update(history=history, audit=audit)
        paper.save_summary(output, summary)
        del adapted
        gc.collect()
        if device.type == 'cuda':
            torch.cuda.empty_cache()
    summary['status'] = 'complete'
    paper.save_summary(output, summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--lr', type=float, default=3e-5)
    parser.add_argument('--subject-weight', type=float, default=10)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.epochs <= 0 or not np.isfinite([args.lr, args.subject_weight]).all() or min(args.lr, args.subject_weight) <= 0:
        parser.error('epochs, lr, and subject-weight must be positive finite values')
    run_experiment(args)
