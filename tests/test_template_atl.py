"""Synthetic safeguards for source-only intended-motion labels (no DB2 needed)."""
import sys
import unittest
from argparse import Namespace
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/deep learning'))
import run_db2_template_atl as experiment
from train import SequenceSplit


class TemplateATLTests(unittest.TestCase):
    def setUp(self):
        self.shape = (len(experiment.PHASE), 10)
        # Medians by person: [2.5, 12.5, 102.5] -> public 12.5, in raw units.
        self.curves = {(s, 1, r): np.full(self.shape, offset + r - 1.)
                       for s, offset in [('S2', 0), ('S3', 10), ('S4', 100)]
                       for r in range(1, 7)}
        n = 6
        self.stream = SequenceSplit(
            x=np.zeros((n, 2, 3), dtype=np.float32), y=np.zeros((n, 10), dtype=np.float32),
            time_s=np.arange(n, dtype=float), alignment_indices=np.arange(n) * 10,
            recording_ids=np.array(['S1_E1_A1.mat'] * n),
            feature_names=['a', 'b', 'c'], target_names=list(experiment.paper.JOINT_ANGLES10_NAMES),
            action_labels=np.ones(n, dtype=int), repetition_labels=np.ones(n, dtype=int),
            stream_start_flags=np.array([True, False, False, False, False, False]),
            expected_alignment_step=10, source_recording_ids=np.array(['S1_E1_A1.mat'] * n))
        self.mask = np.array([False, True, True, True, True, False])
        self.templates = np.broadcast_to((100 + experiment.PHASE * 10)[None, :, None],
                                         (1, *self.shape)).copy()
        self.cues = {'S1_E1_A1.mat': {(1, 1): (10, 40)}}

    def test_balanced_raw_median_and_target_exclusion(self):
        public, within = experiment.balanced_templates(self.curves, ['S2', 'S3', 'S4'], [1], 'S1')
        np.testing.assert_array_equal(public, np.full((1, *self.shape), 12.5))
        np.testing.assert_array_equal(within[:, 0, 0, 0], [2.5, 12.5, 102.5])
        with self.assertRaisesRegex(ValueError, 'held-out'):
            experiment.balanced_templates(self.curves, ['S1', 'S2'], [1], 's1')
        self.curves['S1', 1, 1] = np.zeros(self.shape)
        with self.assertRaisesRegex(ValueError, 'exactly six'):
            experiment.balanced_templates(self.curves, ['S2', 'S3', 'S4'], [1], 'S1')

    def test_missing_repetition_rejected(self):
        del self.curves['S2', 1, 6]
        with self.assertRaisesRegex(ValueError, 'exactly six'):
            experiment.balanced_templates(self.curves, ['S2', 'S3', 'S4'], [1], 'S1')

    def test_hierarchical_median_is_not_flat_median(self):
        values = {'S2': [0, 0, 0, 0, 100, 100], 'S3': [10] * 6,
                  'S4': [-100, -100, -100, 20, 20, 20]}
        curves = {(s, 1, r + 1): np.full(self.shape, v)
                  for s, row in values.items() for r, v in enumerate(row)}
        public, _ = experiment.balanced_templates(curves, list(values), [1], 'S1')
        # Person medians [0, 10, -40] -> 0; pooling all 18 repetitions -> 10.
        np.testing.assert_array_equal(public, 0)
        np.testing.assert_array_equal(np.median(list(curves.values()), axis=0), 10)

    def test_truncated_rest_uses_five_reps_but_active_cue_requires_six(self):
        self.curves['S2', 1, 6][0] = np.nan
        self.curves['S2', 1, 6][experiment.PHASE >= 1] = np.nan
        public, within = experiment.balanced_templates(self.curves, ['S2', 'S3', 'S4'], [1], 'S1')
        self.assertTrue(np.isfinite(public).all())
        np.testing.assert_array_equal(within[0, 0, 0], 2)  # median(0..4), not extrapolated 0..5.
        middle = int(np.flatnonzero(experiment.PHASE > .5)[0])
        self.curves['S2', 1, 6][middle] = np.nan
        with self.assertRaisesRegex(ValueError, 'finite source phase'):
            experiment.balanced_templates(self.curves, ['S2', 'S3', 'S4'], [1], 'S1')

    def test_original_cue_boundaries_and_duplicate_rejection(self):
        self.assertEqual(experiment.cue_segments([0, 1, 1, 0, 1], [0, 1, 1, 0, 2]),
                         {(1, 1): (1, 3), (1, 2): (4, 5)})
        with self.assertRaisesRegex(ValueError, 'noncontiguous'):
            experiment.cue_segments([1, 0, 1], [1, 0, 1])

    def test_cue_only_alignment_and_glove_poisoning_invariance(self):
        y, phase = experiment.weak_stream_targets(self.stream, self.mask, self.templates, [1], self.cues)
        np.testing.assert_allclose(phase[self.mask], [0, 1/3, 2/3, 1])
        np.testing.assert_allclose(y[self.mask, 0], [100, 103+1/3, 106+2/3, 110], atol=1e-5)
        self.assertTrue(np.isnan(y[~self.mask]).all())
        for value in (np.nan, 1e30):
            poisoned = replace(self.stream, y=np.full_like(self.stream.y, value))
            other, _ = experiment.weak_stream_targets(poisoned, self.mask, self.templates, [1], self.cues)
            np.testing.assert_array_equal(y, other)
        np.testing.assert_array_equal(self.stream.y, 0)  # Original truth is never mutated.

    def test_out_of_context_and_empty_mask_rejected(self):
        bad = replace(self.stream, alignment_indices=np.arange(6) * 1000)
        with self.assertRaisesRegex(ValueError, 'outside'):
            experiment.weak_stream_targets(bad, self.mask, self.templates, [1], self.cues)
        with self.assertRaisesRegex(ValueError, 'adaptation mask'):
            experiment.weak_stream_targets(self.stream, np.zeros(6, bool), self.templates, [1], self.cues)

    def test_two_epoch_atl_smoke_with_masked_weak_labels(self):
        paper = experiment.paper
        weak_y, _ = experiment.weak_stream_targets(self.stream, self.mask, self.templates, [1], self.cues)
        # Scale synthetic labels only; real run uses checkpoint-bound source normalizers.
        support = replace(self.stream, y=weak_y / 100)
        source = replace(self.stream, y=np.ones((6, 10), dtype=np.float32))
        config = paper.build_best_cfc_config(seq_len=2, batch_size=2, hidden_units=24,
                                             stateful=True, cuda_graph=False, device='cpu')
        paper.set_random_seed(42)
        model = paper.build_cfc_regressor(input_dim=3, output_dim=10, hidden_units=24)
        self.assertLess(sum(p.numel() for p in model.parameters()) * 4, 400 * 1024)
        base = {k: v.clone() for k, v in model.state_dict().items()}
        adapted, history, audit = paper.fine_tune_head(
            model=model, support_split=support, source_split=source, config=config, y_stats={},
            support_score_mask=self.mask, source_score_mask=self.mask, device=torch.device('cpu'),
            learning_rate=3e-5, dd_lr=3e-5, cfc_atl_lr=3e-5, epochs=2,
            enable_atl=True, atl_subject_weight=10)
        self.assertEqual(len(history), 2)
        self.assertTrue(np.isfinite([list(row.values()) for row in history]).all())
        self.assertEqual(audit['checkpoint_selection'], 'none_final_epoch_returned')
        for k, value in model.state_dict().items():
            torch.testing.assert_close(value, base[k], rtol=0, atol=0)
        self.assertTrue(any(not torch.equal(v, base[k]) for k, v in adapted.state_dict().items()))

        # Poison ALL target truth, rebuild provenance mask and weak labels, then
        # replay the same optimizer seed: labels, losses and final weights agree.
        poisoned = replace(self.stream, y=np.full_like(self.stream.y, 1e30))
        selected = paper.copy_split_by_indices(poisoned, np.flatnonzero(self.mask))
        mask = paper.provenance_score_mask(poisoned, selected)
        np.testing.assert_array_equal(mask, self.mask)
        y2, _ = experiment.weak_stream_targets(poisoned, mask, self.templates, [1], self.cues)
        # Model construction above consumed RNG before the first DD init.
        paper.set_random_seed(42)
        paper.build_cfc_regressor(input_dim=3, output_dim=10, hidden_units=24)
        repeated, history2, _ = paper.fine_tune_head(
            model=model, support_split=replace(poisoned, y=y2 / 100), source_split=source,
            config=config, y_stats={}, support_score_mask=mask, source_score_mask=self.mask,
            device=torch.device('cpu'), learning_rate=3e-5, dd_lr=3e-5, cfc_atl_lr=3e-5,
            epochs=2, enable_atl=True, atl_subject_weight=10)
        self.assertEqual(history, history2)
        for k, value in adapted.state_dict().items():
            torch.testing.assert_close(value, repeated.state_dict()[k], rtol=0, atol=0)

    def test_evaluation_reports_template_fit_not_glove_accuracy_as_primary(self):
        template = np.arange(40, dtype=np.float32).reshape(4, 10)
        measured = template + 100
        evaluation = {'y_true': template, 'y_pred': template.copy(),
                      'metrics': experiment.paper.compute_regression_metrics(template, template)}
        with (patch.object(experiment.paper, 'evaluate_chain', return_value=evaluation),
              patch.object(experiment.np, 'savez_compressed') as save):
            result = experiment.evaluate_arm(
                None, self.stream, self.mask, {}, torch.device('cpu'), Path('unused'), 'weak_template',
                measured_glove=measured)
        self.assertEqual(result['reference'], 'public_template')
        self.assertEqual(result['data_usage'], 'adaptation_training_rows_no_holdout')
        self.assertEqual(result['metrics']['mae_mean'], 0)
        self.assertEqual(result['measured_glove_diagnostic']['metrics']['mae_mean'], 100)
        self.assertEqual(save.call_args.args[0].name, 'weak_template_training_replay.npz')
        np.testing.assert_array_equal(save.call_args.kwargs['y_template'], template)
        np.testing.assert_array_equal(save.call_args.kwargs['y_measured_glove'], measured)

    def test_full_experiment_supervises_six_repetitions_without_query(self):
        paper = experiment.paper
        n = 18
        active = np.arange(n) % 3 != 0  # Each repetition has one rest row, two eligible rows.
        reps = np.where(active, np.arange(n) // 3 + 1, 0)
        target = replace(
            self.stream, x=np.zeros((n, 2, 3), np.float32),
            y=np.full((n, 10), 3, np.float32), time_s=np.arange(n) * .005,
            alignment_indices=np.arange(n) * 10, recording_ids=np.array(['S1_E1_A1.mat'] * n),
            action_labels=active.astype(int), repetition_labels=reps,
            stream_start_flags=np.arange(n) == 0, source_recording_ids=np.array(['S1_E1_A1.mat'] * n))
        source = replace(target, recording_ids=np.array(['S2_E1_A1.mat'] * n),
                         source_recording_ids=np.array(['S2_E1_A1.mat'] * n))
        selected = paper.copy_split_by_indices(target, np.flatnonzero(active))
        source_selected = paper.copy_split_by_indices(source, np.flatnonzero(active))
        config = paper.build_best_cfc_config(
            target_mapping='joint_angles10', stateful=True, cuda_graph=False, device='cpu',
            seq_len=2, batch_size=2, hidden_units=24)
        model = paper.build_cfc_regressor(input_dim=3, output_dim=10, hidden_units=24)
        x_stats = {'method': 'zscore', 'mean': np.zeros(3), 'std': np.ones(3)}
        y_stats = {'method': 'zscore', 'mean': np.zeros(10), 'std': np.ones(10)}
        protocol = {'target_subject': 'S1', 'source_subjects': ['S2'], 'actions': [1],
                    'exercises': ['E1'], 'random_seed': 42, 'target_support_repetitions_per_action': 4,
                    'target_support_query_plan': {'1': {'train_repetitions': [1, 2, 3, 4],
                                                       'test_repetitions': [5, 6]}},
                    'source_supervision_plan': {'S2': paper.describe_all_selected_repetitions(source_selected, actions=(1,))}}
        checkpoint = {'config': asdict(config), 'model_state_dict': model.state_dict(),
                      'target_contract': paper.resolve_target_contract('joint_angles10'),
                      'feature_normalization_stats': x_stats, 'target_normalization_stats': y_stats,
                      'resume_integrity': paper.build_resume_integrity_metadata(
                          x_stats=x_stats, y_stats=y_stats, source_supervised=source_selected,
                          source_stream=source, source_stream_score_mask=active, protocol=protocol)}
        # Actual two-epoch CPU ATL and actual checkpoint integrity validation;
        # only DB2 loading/template construction is substituted with synthetic data.
        with TemporaryDirectory() as temp:
            checkpoint_path, output = Path(temp) / 'base.pt', Path(temp) / 'run'
            torch.save(checkpoint, checkpoint_path)
            with (
                patch.object(paper, 'list_db2_files', return_value=[Path('S1_E1_A1.mat')]),
                patch.object(experiment, 'build_source_templates', return_value=(
                    self.templates / 100, {'template_sha256': 'synthetic'})),
                patch.object(paper, 'load_subject_splits', side_effect=[(source_selected, source), (selected, target)]),
                patch.object(experiment, 'loadmat', return_value={
                    'stimulus': np.repeat(active.astype(int), 10), 'repetition': np.repeat(reps, 10)}),
                patch.object(paper, 'select_repetition_split', side_effect=AssertionError('query split is forbidden')),
                patch.object(paper, 'fine_tune_head', wraps=paper.fine_tune_head) as train,
            ):
                summary = experiment.run_experiment(Namespace(
                    checkpoint=checkpoint_path, output_dir=output, epochs=2, lr=3e-5, subject_weight=10, seed=42))
            self.assertEqual(summary['status'], 'complete')
            self.assertEqual(summary['counts']['target_adaptation'], 12)
            self.assertEqual(summary['counts']['target_context_only'], 6)
            self.assertEqual(summary['protocol']['target_partition'], 'none')
            self.assertNotIn('target_support_query_plan', summary['protocol'])
            self.assertNotIn('target_support_repetitions_per_action', summary['protocol'])
            self.assertEqual(summary['protocol']['target_supervision_plan']['selected_repetitions_by_action']['1'], list(range(1, 7)))
            self.assertEqual(train.call_count, 2)
            for call in train.call_args_list:
                stream, mask = call.kwargs['support_split'], call.kwargs['support_score_mask']
                np.testing.assert_array_equal(mask, active)
                np.testing.assert_array_equal(stream.x, target.x)
                np.testing.assert_array_equal(stream.stream_start_flags, target.stream_start_flags)
                self.assertTrue(np.isfinite(stream.y[reps >= 5]).all())  # Former query now supervised.
                self.assertTrue(np.isnan(stream.y[~active]).all())
            for arm in ('zero_shot', 'weak_template', 'real_glove'):
                self.assertEqual(summary['results'][arm]['reference'], 'public_template')
                self.assertEqual(summary['results'][arm]['data_usage'], 'adaptation_training_rows_no_holdout')
                self.assertTrue((output / f'{arm}_training_replay.npz').is_file())
            self.assertFalse(list(output.glob('*query*')))
            adapted = torch.load(output / 'weak_template_final.pt', map_location='cpu', weights_only=False)
            self.assertEqual(adapted['template_adaptation_provenance']['protocol']['target_partition'], 'none')
            self.assertEqual(checkpoint['resume_integrity']['protocol'], protocol)  # Old binding not rewritten.


if __name__ == '__main__':
    unittest.main()
