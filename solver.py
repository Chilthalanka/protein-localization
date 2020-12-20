import copy
import inspect
import os
import shutil
from typing import Tuple

import pyaml
import torch
import numpy as np
from models import *
import warnings
from sklearn.metrics import matthews_corrcoef, confusion_matrix
from torch.utils.data import DataLoader, RandomSampler, Dataset, Subset
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime

from tqdm import tqdm

from models.loss_functions import JointCrossEntropy
from utils.general import tensorboard_confusion_matrix, padded_permuted_collate, plot_class_accuracies, \
    tensorboard_class_accuracies, annotation_transfer


class Solver():
    def __init__(self, model, args, optim=torch.optim.Adam, loss_func=JointCrossEntropy, weight=None, eval=False):
        self.optim = optim(list(model.parameters()), **args.optimizer_parameters)
        self.args = args
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model = model.to(self.device)
        if args.checkpoint and not eval:
            checkpoint = torch.load(os.path.join(args.checkpoint, 'checkpoint.pt'), map_location=self.device)
            self.writer = SummaryWriter(args.checkpoint)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            self.optim.load_state_dict(checkpoint['optimizer_state_dict'])
            with open(os.path.join(self.writer.log_dir, 'epoch.txt'), "r") as f:  # last epoch not the best epoch
                self.start_epoch = int(f.read()) + 1
            self.max_val_acc = checkpoint['maximum_accuracy']
            self.weight = checkpoint['weight'].to(self.device)
        elif not eval:
            self.start_epoch = 0
            self.max_val_acc = 0  # running accuracy to decide whether or not a new model should be saved
            self.writer = SummaryWriter(
                'runs/{}_{}_{}'.format(args.model_type, args.experiment_name,
                                       datetime.now().strftime('%d-%m_%H-%M-%S')))
            self.weight = weight.to(self.device)

        if args.balanced_loss:
            self.loss_func = loss_func(self.weight)
        else:
            self.loss_func = loss_func()

    def train(self, train_loader: DataLoader, val_loader: DataLoader, eval_data=None):
        """
        Train and simultaneously evaluate on the val_loader and then estimate the stderr on eval_data if it is provided
        Args:
            train_loader: For training
            val_loader: For validation during training
            eval_data: For evaluation and estimating stderr after training

        Returns:

        """
        args = self.args
        epochs_no_improve = 0  # counts every epoch that the validation accuracy did not improve for early stopping
        max_train_acc = 0
        for epoch in range(self.start_epoch, args.num_epochs):  # loop over the dataset multiple times
            self.model.train()
            train_loc_loss, train_sol_loss, train_results = self.predict(train_loader, epoch + 1, optim=self.optim)

            self.model.eval()
            with torch.no_grad():
                val_loc_loss, val_sol_loss, val_results = self.predict(val_loader, epoch + 1)

            loc_train_acc = 100 * np.equal(train_results[:, 0], train_results[:, 1]).sum() / len(train_results)
            loc_val_acc = 100 * np.equal(val_results[:, 0], val_results[:, 1]).sum() / len(val_results)
            with warnings.catch_warnings():  # because sklearns mcc implementation is a little dim
                warnings.filterwarnings("ignore", message="invalid value encountered in double_scalars")
                loc_train_mcc = matthews_corrcoef(train_results[:, 1], train_results[:, 0])
                loc_val_mcc = matthews_corrcoef(val_results[:, 1], val_results[:, 0])

            sol_preds_train = np.equal(train_results[:, 2], train_results[:, 3]) * train_results[:, 4]
            sol_train_acc = 100 * sol_preds_train.sum() / train_results[:, 4].sum()
            sol_preds_val = np.equal(val_results[:, 2], val_results[:, 3]) * val_results[:, 4]
            sol_val_acc = 100 * sol_preds_val.sum() / val_results[:, 4].sum()

            val_acc = sol_val_acc if args.target == 'sol' else loc_val_acc
            train_acc = sol_train_acc if args.target == 'sol' else loc_train_acc

            print('[Epoch %d] VAL accuracy: %.4f%% train accuracy: %.4f%%' % (epoch + 1, val_acc, train_acc))

            tensorboard_class_accuracies(train_results, val_results, self.writer, args, epoch + 1)
            tensorboard_confusion_matrix(train_results, val_results, self.writer, args, epoch + 1)
            self.writer.add_scalars('Loc_Acc', {'train': loc_train_acc, 'val': loc_val_acc}, epoch + 1)
            self.writer.add_scalars('Loc_MCC', {'train': loc_train_mcc, 'val': loc_val_mcc}, epoch + 1)
            self.writer.add_scalars('Loc_Loss', {'train': train_loc_loss, 'val': val_loc_loss}, epoch + 1)
            if args.solubility_loss != 0 or args.target == 'sol':
                self.writer.add_scalars('Sol_Loss', {'train': train_sol_loss, 'val': val_sol_loss}, epoch + 1)
                self.writer.add_scalars('Sol_Acc', {'train': sol_train_acc, 'val': sol_val_acc}, epoch + 1)

            if val_acc >= self.max_val_acc:  # save the model with the best accuracy
                epochs_no_improve = 0
                self.max_val_acc = val_acc
                self.save_checkpoint(epoch + 1)
            else:
                epochs_no_improve += 1

            with open(os.path.join(self.writer.log_dir, 'epoch.txt'), 'w') as file:  # save what the last epoch is
                file.write(str(epoch))

            if train_acc >= max_train_acc:
                max_train_acc = train_acc
            if epochs_no_improve >= args.patience and max_train_acc >= args.min_train_acc:  # stopping criterion
                break

        if eval_data:  # do evaluation on the test data if a eval_data is provided
            # load checkpoint of best model to do evaluation
            checkpoint = torch.load(os.path.join(self.writer.log_dir, 'checkpoint.pt'), map_location=self.device)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            self.evaluation(eval_data)

    def predict(self, data_loader: DataLoader, epoch: int = None, optim: torch.optim.Optimizer = None) -> \
            Tuple[float, float, np.ndarray]:
        """
        get predictions for data in dataloader and do backpropagation if an optimizer is provided
        Args:
            data_loader: pytorch dataloader from which the batches will be taken
            epoch: optional parameter for logging
            optim: pytorch optimiz. If this is none, no backpropagation is done

        Returns:
            loc_loss: the average of the localization loss accross all batches
            sol_loss: the average of the solubility loss across all batches
            results: localizations # [n_train_proteins, 2] predictions in first and loc in second position
        """
        args = self.args
        results = []  # prediction and corresponding localization
        running_loc_loss = 0
        running_sol_loss = 0
        for i, batch in enumerate(data_loader):
            embedding, loc, sol, metadata = batch  # get localization and solubility label
            embedding, loc, sol, sol_known = embedding.to(self.device), loc.to(self.device), sol.to(self.device), \
                                             metadata['solubility_known'].to(self.device)
            # create mask corresponding to the zero padding used for the shorter sequecnes in the batch. All values corresponding to padding are False and the rest is True.
            mask = torch.arange(metadata['length'].max())[None, :] < metadata['length'][:, None]  # [batchsize, seq_len]

            prediction = self.model(embedding, mask.to(self.device))
            loss, loc_loss, sol_loss = self.loss_func(prediction, loc, sol, sol_known, args)
            if optim:  # run backpropagation if an optimizer is provided
                loss.backward()
                self.optim.step()
                self.optim.zero_grad()

            sol_pred = torch.max(prediction[..., -2:], dim=1)[1]  # get indices of the highest value for sol

            if args.target == 'sol':
                loc_pred = sol_pred  # ignore loc predictions
            else:
                loc_pred = torch.max(prediction[..., :10], dim=1)[1]  # get indices of the highest value for loc
            results.append(torch.stack((loc_pred, loc, sol_pred, sol, sol_known), dim=1).detach().cpu().numpy())
            loc_loss_item = loc_loss.item()
            running_loc_loss += loc_loss_item
            running_sol_loss += sol_loss.item()
            if i % args.log_iterations == args.log_iterations - 1:  # log every log_iterations
                if epoch:
                    print('Epoch %d ' % (epoch), end=' ')
                print('[Iter %5d/%5d] %s: loc loss: %.7f, loc accuracy: %.4f%%' % (
                    i + 1, len(data_loader), 'Train' if optim else 'Val', loc_loss_item,
                    100 * (loc_pred == loc).sum().item() / args.batch_size))

        running_loc_loss /= len(data_loader)
        running_sol_loss /= len(data_loader)
        return running_loc_loss, running_sol_loss, np.concatenate(results)  # [n_train_proteins, 2] pred and loc

    def evaluation(self, eval_dataset: Dataset, filename: str = '', lookup_dataset: Dataset = None,
                   accuracy_threshold=0.81):
        """
        Estimate the standard error on the provided dataset and write it to evaluation_val.txt in the run directory
        Args:
            eval_dataset: the dataset for which to estimate the stderr
            filename: string to append to the produced visualizations
            lookup_dataset: dataset used for embedding space similarity annotation transfer. If it is none, no annotation transfer will be done
            accuracy_threshold: accuracy to determine the distance below which the annotation transfer is used.

        Returns:

        """
        if self.args.target != 'sol' and lookup_dataset:
            low_distance_results, high_distance_indices = annotation_transfer(eval_dataset, lookup_dataset,
                                                                              accuracy_threshold,
                                                                              self.writer,
                                                                              filename)
            # only keep the indices for which we have high confidence
            eval_dataset = Subset(eval_dataset, high_distance_indices)

        self.model.eval()
        if len(eval_dataset[0][0].shape) == 2:  # if we have per residue embeddings they have an additional length dim
            collate_function = padded_permuted_collate
        else:  # if we have reduced sequence wise embeddings use the default collate function by passing None
            collate_function = None

        sampler = RandomSampler(eval_dataset, replacement=True)
        data_loader = DataLoader(eval_dataset, batch_size=self.args.batch_size, sampler=sampler,
                                 collate_fn=collate_function)

        data_loader = DataLoader(eval_dataset, batch_size=self.args.batch_size, shuffle=False, collate_fn=collate_function)
        loc_loss, sol_loss, results = self.predict(data_loader)
        mccs = []
        accuracies = []
        supervised_accuracies = []
        unsupervised_accuracies = []
        class_accuracies = []
        with torch.no_grad():
            for i in tqdm(range(self.args.n_draws)):

                if self.args.target == 'sol':
                    accuracies.append(100 * np.equal(results[:, 2], results[:, 3]).sum() / len(results))
                    mccs.append(matthews_corrcoef(results[:, 3], results[:, 2]))

                else:
                    if lookup_dataset:
                        unsupervised_accuracies.append(
                            100 * np.equal(low_distance_results[:, 0], low_distance_results[:, 1]).sum() / len(
                                low_distance_results))
                        supervised_accuracies.append(
                            100 * np.equal(results[:, 0], results[:, 1]).sum() / len(results))
                        results = np.concatenate([low_distance_results[:, :2], results[:, :2]])
                    accuracies.append(100 * np.equal(results[:, 0], results[:, 1]).sum() / len(results))
                    mccs.append(matthews_corrcoef(results[:, 1], results[:, 0]))
                    conf = confusion_matrix(results[:, 1], results[:, 0])
                    class_accuracies.append(np.diag(conf) / conf.sum(1))

        accuracy = np.mean(accuracies)
        accuracy_stderr = np.std(accuracies)
        mcc = np.mean(mccs)
        mcc_stderr = np.std(mccs)
        class_accuracy = np.mean(np.array(class_accuracies), axis=0)
        class_accuracy_stderr = np.std(np.array(class_accuracies), axis=0)
        results_string = 'Number of draws: {} \n' \
                         'Accuracy: {:.2f}% \n' \
                         'Accuracy stderr: {:.2f}%\n' \
                         'MCC: {:.4f}\n' \
                         'MCC stderr: {:.4f}\n'.format(self.args.n_draws, accuracy, accuracy_stderr, mcc, mcc_stderr)
        if lookup_dataset:  # if we did lookups we append the individual accuracies to the results file
            unsupervised_accuracy = np.mean(np.array(unsupervised_accuracies), axis=0)
            supervised_accuracy = np.mean(np.array(supervised_accuracies), axis=0)
            results_string += 'unsupervised accuracy: {:.4f}\n' \
                              'supervised accuracy: {:.4f}\n'.format(unsupervised_accuracy, supervised_accuracy)

        with open(os.path.join(self.writer.log_dir, 'evaluation_' + filename + '.txt'), 'w') as file:
            file.write(results_string)
        print(results_string)
        plot_class_accuracies(class_accuracy, class_accuracy_stderr,
                              os.path.join(self.writer.log_dir, 'class_accuracies_' + filename + '.png'), self.args)

    def save_checkpoint(self, epoch: int):
        """
        Saves checkpoint of model in the logdir of the summarywriter/ in the used rundir
        Args:
            epoch: current epoch from which the run will be continued if it is loaded

        Returns:

        """
        run_dir = self.writer.log_dir
        torch.save({
            'epoch': epoch,
            'weight': self.weight,
            'maximum_accuracy': self.max_val_acc,
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optim.state_dict(),
        }, os.path.join(run_dir, 'checkpoint.pt'))
        train_args = copy.copy(self.args)
        train_args.config = train_args.config.name
        pyaml.dump(train_args.__dict__, open(os.path.join(run_dir, 'train_arguments.yaml'), 'w'))
        shutil.copyfile(self.args.config.name, os.path.join(run_dir, os.path.basename(self.args.config.name)))

        # Get the class of the used model (works because of the "from models import *" calling the init.py in the models dir)
        model_class = globals()[type(self.model).__name__]
        source_code = inspect.getsource(model_class)  # Get the sourcecode of the class of the model.
        file_name = os.path.basename(inspect.getfile(model_class))
        with open(os.path.join(run_dir, file_name), "w") as f:
            f.write(source_code)
