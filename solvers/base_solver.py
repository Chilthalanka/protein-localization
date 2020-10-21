import os
from typing import Tuple

import torch
import numpy as np
from sklearn.metrics import matthews_corrcoef
from torch.utils.data import DataLoader, RandomSampler, Dataset
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime

from tqdm import tqdm

from models.loss_functions import JointCrossEntropy
from utils.general import tensorboard_confusion_matrix, experiment_checkpoint, padded_permuted_collate


class BaseSolver():
    def __init__(self, model, args, optim=torch.optim.Adam, loss_func=JointCrossEntropy, eval=False):
        self.optim = optim(list(model.parameters()), **args.optimizer_parameters)
        self.loss_func = loss_func()
        self.args = args
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model = model.to(self.device)
        if args.checkpoint and not eval:
            checkpoint = torch.load(os.path.join(args.checkpoint, 'checkpoint.pt'), map_location=self.device)
            self.writer = SummaryWriter(args.checkpoint)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            self.optim.load_state_dict(checkpoint['optimizer_state_dict'])
            self.start_epoch = checkpoint['epoch']
        elif not eval:
            self.start_epoch = 0
            self.writer = SummaryWriter(
                'runs/{}_{}_{}'.format(args.model_type, args.experiment_name,
                                       datetime.now().strftime('%d-%m_%H-%M-%S')))

    def train(self, train_loader: DataLoader, val_loader: DataLoader, eval_data=None):
        """
        Train and simultaneosly evaluate on the val_loader and then estimate the stderr on eval_data if it is provided
        Args:
            train_loader: For training
            val_loader: For validation during training
            eval_data: For evaluation and estimating stderr after training

        Returns:

        """
        args = self.args
        epochs_no_improve = 0  # counts every epoch that the validation accuracy did not improve for early stopping
        maximum_accuracy = 0  # running accuracy to decide whether or not a new model should be saved
        for epoch in range(self.start_epoch, args.num_epochs):  # loop over the dataset multiple times
            self.model.train()
            train_loc_loss, train_sol_loss, train_results = self.predict(train_loader, epoch + 1, optim=self.optim)

            self.model.eval()
            with torch.no_grad():
                val_loc_loss, val_sol_loss, val_results = self.predict(val_loader, epoch + 1)

            train_acc = 100 * np.equal(train_results[:, 0], train_results[:, 1]).sum() / len(train_results)
            val_acc = 100 * np.equal(val_results[:, 0], val_results[:, 1]).sum() / len(val_results)
            train_mcc = matthews_corrcoef(train_results[:, 1], train_results[:, 0])
            val_mcc = matthews_corrcoef(val_results[:, 1], val_results[:, 0])

            sol_preds_train = np.equal(train_results[:, 2], train_results[:, 3]) * train_results[:, 4]
            sol_train_acc = 100 * sol_preds_train.sum() / train_results[:, 4].sum()
            sol_preds_val = np.equal(val_results[:, 2], val_results[:, 3]) * val_results[:, 4]
            sol_val_acc = 100 * sol_preds_val.sum() / val_results[:, 4].sum()

            print('[Epoch %d] VAL accuracy: %.4f%% train accuracy: %.4f%%' % (epoch + 1, val_acc, train_acc))

            # write to tensorboard
            tensorboard_confusion_matrix(train_results, val_results, self.writer, epoch + 1)
            self.writer.add_scalars('Loc_Acc', {'train': train_acc, 'val': val_acc}, epoch + 1)
            self.writer.add_scalars('Loc_MCC', {'train': train_mcc, 'val': val_mcc}, epoch + 1)
            self.writer.add_scalars('Loc_Loss', {'train': train_loc_loss, 'val': val_loc_loss}, epoch + 1)
            if args.solubility_loss != 0:
                self.writer.add_scalars('Sol_Loss', {'train': train_sol_loss, 'val': val_sol_loss}, epoch + 1)
                self.writer.add_scalars('Sol_Acc', {'train': sol_train_acc, 'val': sol_val_acc}, epoch + 1)

            if val_acc >= maximum_accuracy:  # save the model with the best accuracy
                epochs_no_improve = 0
                maximum_accuracy = val_acc
                experiment_checkpoint(self.writer.log_dir, self.model, self.optim, epoch + 1, args)
            else:
                epochs_no_improve += 1

            if epochs_no_improve >= args.patience:
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

            prediction = self.model(embedding)
            loss, loc_loss, sol_loss = self.loss_func(prediction, loc, sol, sol_known, args)
            if optim:  # run backpropagation if an optimizer is provided
                loss.backward()
                self.optim.step()
                self.optim.zero_grad()

            loc_pred = torch.max(prediction[..., :10], dim=1)[1]  # get indices of the highest value for loc
            sol_pred = torch.max(prediction[..., -2:], dim=1)[1]  # get indices of the highest value for sol
            results.append(
                torch.stack((loc_pred, loc, sol_pred, sol, sol_known), dim=1).detach().cpu().numpy())
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

    def evaluation(self, dataset: Dataset):
        self.model.eval()
        if len(dataset[0][0].shape) == 2:  # if we have per residue embeddings they have an additional length dim
            collate_function = padded_permuted_collate
        else:  # if we have reduced sequence wise embeddings use the default collate function by passing None
            collate_function = None

        sampler = RandomSampler(dataset, replacement=True)
        data_loader = DataLoader(dataset, batch_size=self.args.batch_size, sampler=sampler, collate_fn=collate_function)
        mccs = []
        accuracies = []
        with torch.no_grad():
            for i in tqdm(range(self.args.n_draws)):
                loc_loss, sol_loss, results = self.predict(data_loader)
                accuracies.append(100 * np.equal(results[:, 0], results[:, 1]).sum() / len(results))
                mccs.append(matthews_corrcoef(results[:, 1], results[:, 0]))

        accuracy = np.mean(accuracies)
        accuracy_stderr = np.std(accuracies)
        mcc = np.mean(mccs)
        mcc_stderr = np.std(mccs)
        results_string = 'Accuracy: {:.2f}% \n' \
                         'Accuracy stderr: {:.2f}%\n' \
                         'MCC: {:.4f}\n' \
                         'MCC stderr: {:.4f}\n'.format(accuracy, accuracy_stderr, mcc, mcc_stderr)
        with open(os.path.join(self.writer.log_dir, 'evaluation.txt'), 'w') as file:
            file.write(results_string)
        print(results_string)
